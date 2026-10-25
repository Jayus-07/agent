"""模型切换的存储边界：真实 PG 事务与向量，模型计算用确定性替身。"""
from __future__ import annotations

import uuid

import pytest

from backend.rag.indexing import embedding_migration as migration
from backend.rag.vectorstore.pgvector_store import (
    EMBEDDING_DIM, IndexEmbeddingMismatchError, PgVectorKnowledgeStore,
    _meta_table_name,
)


class Embedding:
    def __init__(self, name, fingerprint):
        self.model_name = name
        self._index_fingerprint = fingerprint
        self._provider_id = "test-provider"

    def embed_documents(self, texts):
        offset = 0 if self.model_name == "old" else 1
        return [[1.0 if i == offset else 0.0 for i in range(EMBEDDING_DIM)]
                for _ in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture
def data(monkeypatch):
    prefix = "migration_" + uuid.uuid4().hex[:10] + "_"
    monkeypatch.setenv("VECTOR_PG_TABLE_PREFIX", prefix)
    old = Embedding("old", "old-fingerprint")
    new = Embedding("new", "new-fingerprint")
    stores = [PgVectorKnowledgeStore(name, old)
              for name in ("chroma", "doc_db", "router_index")]
    for i, store in enumerate(stores):
        store.add_texts([f"文档{i}"], [{"doc_id": f"doc{i}", "tenant_id": "tenant-a"}])
    try:
        yield stores, new
    finally:
        with migration.connection() as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {prefix}rag_vectors")
            cur.execute(f"DROP TABLE IF EXISTS {prefix}rag_index_meta")


def stage_rows(new, *, limit=None):
    """与任务同样保留源文本和元数据快照，候选集合不对检索可见。"""
    stage = "embedding::cand:test"
    candidate = PgVectorKnowledgeStore(stage, new)
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id,collection,content,metadata::text FROM {migration._table()} "
            "WHERE position('::cand:' in collection)=0 ORDER BY id"
        )
        source = cur.fetchall()
    rows = source if limit is None else source[:limit]
    texts = [row[2] for row in rows]
    metas = [{"source_id": row[0], "source_collection": row[1],
              "source_metadata": row[3]} for row in rows]
    prepared = candidate._texts_to_rows(
        texts, metas, new.embed_documents(texts), [stage + row[0] for row in rows],
    )
    candidate._upsert_rows(prepared)
    return stage, len(source)


def publish(stage, count):
    with migration.connection() as conn:
        migration._publish(conn, stage, "new", "new-fingerprint", "test-provider",
                           EMBEDDING_DIM, count)


def test_partial_rebuild_never_unblocks_or_relabels_main(data):
    stores, new = data
    migration.mark_required()
    stage, count = stage_rows(new, limit=1)
    assert stores[0].get_index_meta()["status"] == "rebuild_required"
    with pytest.raises(IndexEmbeddingMismatchError):
        stores[0].similarity_search("查询", k=1)
    with pytest.raises(ValueError, match="不完整"):
        publish(stage, count)
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT embedding_model,status FROM {_meta_table_name()} "
                    "WHERE collection='chroma'")
        assert cur.fetchone() == ("old", "rebuild_required")


def test_query_cannot_use_old_vector_if_publication_happens_during_model_call(data, monkeypatch):
    stores, new = data
    stage, count = stage_rows(new)
    old = stores[0].embedding_function
    original = old.embed_query

    def publish_during_call(text):
        vector = original(text)
        migration.mark_required()
        publish(stage, count)
        return vector

    monkeypatch.setattr(old, "embed_query", publish_during_call)
    with pytest.raises(IndexEmbeddingMismatchError):
        stores[0].similarity_search("切换窗口内查询")


def test_atomic_publish_covers_router_documents_and_stale_worker(data):
    stores, new = data
    migration.mark_required()
    stage, count = stage_rows(new)
    publish(stage, count)
    for old_store in stores:
        assert old_store.get_index_meta()["embeddingModel"] == "new"
        assert old_store.get_index_meta()["status"] == "ready"
        with pytest.raises(IndexEmbeddingMismatchError):
            old_store.add_texts(["旧进程不得再写"], [{"doc_id": "stale"}])
        current = PgVectorKnowledgeStore(old_store._collection, new)
        assert len(current.similarity_search("查询", k=1)) == 1
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*),count(DISTINCT metadata->>'tenant_id') "
                    f"FROM {migration._table()}")
        assert cur.fetchone() == (3, 1)
        cur.execute(f"SELECT count(*) FROM {migration._table()} "
                    "WHERE (embedding::real[])[1] = 1")
        assert cur.fetchone()[0] == 0


def test_new_collection_and_candidate_promotion_cannot_bypass_barrier(data):
    stores, new = data
    old_candidate = stores[0].clone_for_candidate("old-generation")
    old_candidate.add_texts(["旧候选"], [{"doc_id": "old-candidate"}])
    migration.mark_required()
    empty = PgVectorKnowledgeStore("created-after-switch", new)
    with pytest.raises(IndexEmbeddingMismatchError):
        empty.add_texts(["新增文档"], [{"doc_id": "new"}])
    with pytest.raises(IndexEmbeddingMismatchError):
        stores[0].promote_collection(old_candidate._collection)


def test_source_changed_during_embedding_rolls_back_publication(data):
    stores, new = data
    migration.mark_required()
    stage, count = stage_rows(new)
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {migration._table()} SET content='已修改' "
                    "WHERE collection='chroma'")
    with pytest.raises(ValueError, match="源文本已变化"):
        publish(stage, count)
    assert stores[0].get_index_meta()["status"] == "rebuild_required"


def test_missing_metadata_does_not_adopt_old_vectors(data):
    stores, new = data
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {_meta_table_name()} WHERE collection='chroma'")
    untrusted = PgVectorKnowledgeStore("chroma", new)
    with pytest.raises(IndexEmbeddingMismatchError):
        untrusted.similarity_search("查询", k=1)
    with pytest.raises(IndexEmbeddingMismatchError):
        untrusted.add_texts(["不可认领"], [{"doc_id": "new"}])


@pytest.mark.parametrize("vectors,count,dimension", [
    ([[1, 2]], 1, 1024), ([[float("nan")]], 1, 1),
    ([[float("inf")]], 1, 1), ([[True]], 1, 1), ([], 1, 1),
    ([[0.0]], 1, 1), ([None], 1, 1),
])
def test_invalid_model_outputs_are_rejected(vectors, count, dimension):
    with pytest.raises(ValueError, match="输出无效"):
        migration.validate_vectors(vectors, count, dimension)


def test_equal_dimension_is_not_equal_model_space(data):
    stores, new = data
    changed = PgVectorKnowledgeStore("chroma", new)
    with pytest.raises(IndexEmbeddingMismatchError):
        changed.add_texts(["同为1024维的新模型"], [{"doc_id": "new"}])
    assert stores[0].get_index_meta()["embeddingModel"] == "old"


def test_same_name_with_changed_configuration_cannot_reuse_old_space(data):
    stores, _ = data
    changed = PgVectorKnowledgeStore("chroma", Embedding("old", "changed-weights"))
    with pytest.raises(IndexEmbeddingMismatchError):
        changed.similarity_search("查询", k=1)


def test_model_switch_cannot_cross_document_publication_commit(data):
    stores, _ = data
    with stores[0].publication_guard():
        with pytest.raises(ValueError, match="文档正在发布"):
            migration.mark_required()
    migration.mark_required()
    assert stores[0].get_index_meta()["status"] == "rebuild_required"


def test_empty_space_activation_needs_no_rebuild_but_blocks_stale_writer(data):
    stores, new = data
    for store in stores:
        store.delete(where={"tenant_id": "tenant-a"})
    assert migration.storage_snapshot()["vectors"] == 0
    migration.activate_empty_space("new", "new-fingerprint", "test-provider", EMBEDDING_DIM)
    current = PgVectorKnowledgeStore("chroma", new)
    current.add_texts(["首份新文档"], [{"doc_id": "first"}])
    assert current.count() == 1
    with pytest.raises(IndexEmbeddingMismatchError):
        stores[0].add_texts(["过期进程"], [{"doc_id": "old"}])


def test_deployment_dimension_configuration_must_match_real_column(data, monkeypatch):
    monkeypatch.setattr(migration, "EMBEDDING_DIM", 1536)
    with pytest.raises(ValueError, match="VECTOR_PG_DIM"):
        migration.storage_snapshot()


def test_failed_metadata_initialization_can_recover_without_restart(data):
    stores, _ = data
    with migration.connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE {_meta_table_name()}")
    with pytest.raises(IndexEmbeddingMismatchError):
        stores[0].similarity_search("查询", k=1)
    migration.mark_required()
    assert stores[0].get_index_meta()["status"] == "rebuild_required"
