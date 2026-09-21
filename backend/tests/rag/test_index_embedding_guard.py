"""test_index_embedding_guard.py — Embedding 与索引一致性门禁（治理 2026-09-22）。

两部分（与 test_pgvector_store.py 同模式）：
  1. 纯单测：模型匹配判定 / 健康状态分类 / 异常码
  2. 真 PG 集成冒烟（本地 PG 可达时运行；VECTOR_PG_TABLE_PREFIX=test_ 隔离；
     embedding 用确定性 stub，不触网）：
     - 写入即登记索引元数据
     - 换 embedding 模型查询 → IndexEmbeddingMismatchError（禁止检索）
     - 无元数据存量索引首次查询 → 采纳基线放行
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from backend.rag.vectorstore.pgvector_store import (
    EMBEDDING_DIM,
    IndexEmbeddingMismatchError,
    PgVectorKnowledgeStore,
    _meta_model_matches,
    _runtime_embedding_identity,
)


# ======================= 纯单测 =======================

class TestMetaModelMatches:
    def test_same_model(self):
        assert _meta_model_matches("text-embedding-v3", "text-embedding-v3")

    def test_diff_model(self):
        assert not _meta_model_matches("text-embedding-v3", "Qwen/Qwen3-8B")

    def test_empty_tolerant(self):
        """身份信息缺失（旧数据/异常实例）不误伤。"""
        assert _meta_model_matches("", "text-embedding-v3")
        assert _meta_model_matches("text-embedding-v3", "")
        assert _meta_model_matches("", "")


class TestRuntimeIdentity:
    def test_read_from_instance_fields(self):
        class _E:
            _model_name = "emb-A"
            _provider = "cloud"

        assert _runtime_embedding_identity(_E()) == {
            "provider": "cloud", "model": "emb-A"}


class TestMismatchError:
    def test_code(self):
        err = IndexEmbeddingMismatchError("emb-A", "emb-B", "chroma")
        assert err.code == "INDEX_EMBEDDING_MISMATCH"
        assert "emb-A" in str(err) and "emb-B" in str(err)


# ======================= 真 PG 集成冒烟 =======================

def _pg_available() -> bool:
    try:
        import psycopg2
        from backend.config.database import VECTOR_PG_CONFIG
        conn = psycopg2.connect(connect_timeout=3, **VECTOR_PG_CONFIG)
        conn.close()
        return True
    except Exception:
        return False


class _StubEmbedding:
    """确定性伪嵌入 + 可注入的模型身份（_model_name 供身份读取）。"""

    def __init__(self, model_name: str, dim: int = EMBEDDING_DIM):
        self._model_name = model_name
        self._provider = "cloud"
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        rng = np.random.RandomState(abs(hash(text)) % (2 ** 31))
        v = rng.rand(self.dim).astype(np.float32)
        return (v / np.linalg.norm(v)).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class _Doc:
    def __init__(self, page_content: str, metadata: dict | None = None):
        self.page_content = page_content
        self.metadata = metadata or {}


def _cleanup(collection: str) -> None:
    import psycopg2
    from backend.config.database import VECTOR_PG_CONFIG

    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM test_rag_vectors WHERE collection = %s", (collection,))
    try:
        # meta 表由 store 懒建；首跑可能尚不存在
        cur.execute("DELETE FROM test_rag_index_meta WHERE collection = %s", (collection,))
    except psycopg2.errors.UndefinedTable:
        pass
    conn.close()


@pytest.fixture()
def guard_env(tmp_path):
    if not _pg_available():
        pytest.skip("本地 PG 不可达，跳过 pgvector 集成冒烟")
    os.environ["VECTOR_PG_TABLE_PREFIX"] = "test_"
    collection = f"chroma_guard_{os.getpid()}"
    _cleanup(collection)
    yield str(tmp_path / collection), collection
    _cleanup(collection)
    os.environ.pop("VECTOR_PG_TABLE_PREFIX", None)


class TestIndexEmbeddingGuardIntegration:
    def test_write_records_meta_and_query_passes(self, guard_env):
        from backend.rag.vectorstore.pgvector_store import _meta_table_name
        import psycopg2
        from backend.config.database import VECTOR_PG_CONFIG

        path, collection = guard_env
        store = PgVectorKnowledgeStore(
            persist_directory=path, embedding_function=_StubEmbedding("emb-A"))
        store.add_texts(["库存管理制度第一条"], [{"doc_id": "d1"}])

        conn = psycopg2.connect(**VECTOR_PG_CONFIG)
        cur = conn.cursor()
        cur.execute(
            f"SELECT embedding_model, status FROM {_meta_table_name()} WHERE collection = %s",
            (collection,))
        row = cur.fetchone()
        conn.close()
        assert row is not None, "写入后必须登记索引元数据"
        assert row[0] == "emb-A" and row[1] == "ready"

        # 同模型查询 → 放行
        docs = store.similarity_search("库存", k=1)
        assert isinstance(docs, list)

    def test_mismatch_blocks_query(self, guard_env):
        """索引 emb-A、运行时 emb-B → 查询必须失败并返回 INDEX_EMBEDDING_MISMATCH。"""
        path, collection = guard_env
        store = PgVectorKnowledgeStore(
            persist_directory=path, embedding_function=_StubEmbedding("emb-A"))
        store.add_texts(["库存管理制度第一条"], [{"doc_id": "d1"}])

        switched = PgVectorKnowledgeStore(
            persist_directory=path, embedding_function=_StubEmbedding("emb-B"))
        with pytest.raises(IndexEmbeddingMismatchError) as exc_info:
            switched.similarity_search_with_score("库存", k=1)
        assert exc_info.value.code == "INDEX_EMBEDDING_MISMATCH"

    def test_legacy_index_adopts_baseline(self, guard_env):
        """无元数据的存量索引：首次查询采纳运行时身份为基线并放行（只发生一次）。"""
        from backend.rag.vectorstore.pgvector_store import _meta_table_name
        import psycopg2
        from backend.config.database import VECTOR_PG_CONFIG

        path, collection = guard_env
        store = PgVectorKnowledgeStore(
            persist_directory=path, embedding_function=_StubEmbedding("emb-A"))
        store.add_texts(["库存管理制度第一条"], [{"doc_id": "d1"}])
        # 手工清掉元数据，模拟 034 之前的存量索引
        conn = psycopg2.connect(**VECTOR_PG_CONFIG)
        conn.autocommit = True
        conn.cursor().execute(
            f"DELETE FROM {_meta_table_name()} WHERE collection = %s", (collection,))
        conn.close()

        docs = store.similarity_search("库存", k=1)
        assert isinstance(docs, list)  # 放行
        conn = psycopg2.connect(**VECTOR_PG_CONFIG)
        cur = conn.cursor()
        cur.execute(
            f"SELECT embedding_model, status FROM {_meta_table_name()} WHERE collection = %s",
            (collection,))
        row = cur.fetchone()
        conn.close()
        assert row is not None and row[0] == "emb-A"  # 基线已采纳

    def test_rebuild_required_status_blocks_query(self, guard_env):
        """rebuild_required 状态（embedding 角色被换）同样拒绝查询。"""
        from backend.rag.vectorstore.pgvector_store import _meta_table_name
        import psycopg2
        from backend.config.database import VECTOR_PG_CONFIG

        path, collection = guard_env
        store = PgVectorKnowledgeStore(
            persist_directory=path, embedding_function=_StubEmbedding("emb-A"))
        store.add_texts(["库存管理制度第一条"], [{"doc_id": "d1"}])
        store.mark_rebuild_required()

        with pytest.raises(IndexEmbeddingMismatchError):
            store.similarity_search_with_score("库存", k=1)
