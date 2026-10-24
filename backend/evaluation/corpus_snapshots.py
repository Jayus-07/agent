"""保留独立向量 collection、全文父块和 BM25，供固定语料复跑。"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

from backend.evaluation.dataset import imports as assets


class EmptyCorpusError(ValueError):
    """知识库没有已发布文档，无法创建固定语料快照。"""


def corpus_content_hash(record: dict) -> str:
    """对语料语义内容计算稳定指纹，排除快照自身和存储分配信息。"""
    stable_fulltext = [
        {key: value for key, value in row.items() if key not in {"id", "created_at"}}
        for row in record["fulltext"]
    ]
    return assets.digest(
        {
            "kb_id": record["kb_id"],
            "documents": record["documents"],
            "visible_ids": record["visible_ids"],
            "capture_warnings": record["capture_warnings"],
            "registry_vector_mapping_complete": record["registry_vector_mapping_complete"],
            "fulltext": stable_fulltext,
            "vector_manifests": record["vector_manifests"],
        }
    )


def capture(kb_id: str, actor: str, *, candidate=None) -> dict:
    from psycopg2.extras import RealDictCursor

    from backend.config import CHROMA_PATH, DOC_DB_PATH
    from backend.config.database import DOC_REGISTRY_PG_CONFIG, RAG_STORES_PG_CONFIG, VECTOR_PG_CONFIG
    from backend.rag.embedding_singleton import get_embedding
    from backend.rag.indexing.chunk_store import get_chunk_store
    from backend.rag.indexing.doc_registry import DocumentRegistry
    from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore, _meta_table_name

    if not (DOC_REGISTRY_PG_CONFIG == RAG_STORES_PG_CONFIG == VECTOR_PG_CONFIG):
        raise ValueError("固定快照要求 registry、全文与向量存储位于同一数据库")
    registry = DocumentRegistry()
    # 先用注册表快速拒绝空知识库，避免在无语料时初始化 Embedding
    # （云端模式可能因缺少凭据失败，掩盖真正的输入校验错误）。
    if not registry.list_by_kb(kb_id):
        raise EmptyCorpusError("知识库没有已发布文档")
    chunks = get_chunk_store()
    embedding = get_embedding()
    stores = [PgVectorKnowledgeStore(path, embedding) for path in (CHROMA_PATH, DOC_DB_PATH)]
    identity = uuid.uuid4().hex[:24]
    names = [f"eval-{identity}-{index}" for index in range(2)]
    with stores[0]._conn() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SET LOCAL lock_timeout = '5s'")
        # 与上传/删除的各次写入互斥；只在复制期间持锁，不覆盖模型调用。
        cur.execute(f"LOCK TABLE {registry._table}, {chunks._table}, {stores[0]._table} IN SHARE ROW EXCLUSIVE MODE")
        cur.execute(f"SELECT * FROM {registry._table} WHERE kb_id=%s AND status='active' ORDER BY doc_id", (kb_id,))
        documents = [dict(row) for row in cur.fetchall()]
        replaced = []
        if candidate:
            replaced = [candidate["doc_id"], candidate.get("old_doc_id") or candidate["doc_id"]]
            documents = [row for row in documents if row["doc_id"] not in replaced]
            documents.append(
                {
                    "doc_id": candidate["doc_id"],
                    "file_name": candidate["file_name"],
                    "file_path": candidate["file_path"],
                    "file_hash": candidate["file_hash"],
                    "version_id": candidate["generation"],
                    "active_generation": candidate["generation"],
                    "index_generation": candidate["generation"],
                    "status": "active",
                    "kb_id": kb_id,
                }
            )
        if not documents:
            raise EmptyCorpusError("知识库没有已发布文档")
        doc_ids = [row["doc_id"] for row in documents]
        cur.execute(
            f"""SELECT DISTINCT r.doc_id FROM {registry._table} r
            WHERE r.doc_id=ANY(%s) AND r.status='active'
            AND (r.expire_at IS NULL OR r.expire_at>to_char(current_date,'YYYY-MM-DD'))
            AND NOT EXISTS (SELECT 1 FROM {registry._table} other WHERE other.doc_id=r.doc_id
                AND (other.status IN ('pending_review','deprecated') OR
                     (other.status='active' AND other.expire_at<=to_char(current_date,'YYYY-MM-DD'))))""",
            (doc_ids,),
        )
        visible_ids = [row["doc_id"] for row in cur.fetchall()]
        if candidate:
            visible_ids = [value for value in visible_ids if value not in replaced] + [candidate["doc_id"]]
        cur.execute(
            f"SELECT * FROM {chunks._table} WHERE doc_id=ANY(%s) AND kb_id=%s ORDER BY doc_id,chunk_index",
            (doc_ids, kb_id),
        )
        fulltext = [dict(row) for row in cur.fetchall()]
        if candidate:
            fulltext = [row for row in fulltext if row["doc_id"] not in replaced]
            rows = candidate["stores"].chunk_store._rows_by_doc.get(candidate["doc_id"], [])
            if not rows:
                raise ValueError("候选全文缓冲缺失，不能执行发布评测")
            fulltext.extend({**row, "doc_id": candidate["doc_id"]} for row in rows)
        manifests = []
        capture_warnings = []
        for source, target in zip(stores, names):
            cur.execute(
                f"SELECT count(*) AS n FROM {source._table} WHERE collection=%s AND metadata->>'kb_id'=%s",
                (source._collection, kb_id),
            )
            if cur.fetchone()["n"] > 100000:
                raise ValueError("单个固定快照超过 100000 向量，请先缩小评测知识库")
            candidate_source = (
                (candidate["stores"].vectordb if source is stores[0] else candidate["stores"].doc_db)._collection
                if candidate
                else ""
            )
            cur.execute(
                f"SELECT id,doc_id FROM {source._table} WHERE collection=%s AND metadata->>'kb_id'=%s",
                (source._collection, kb_id),
            )
            live_ids = {}
            for row in cur.fetchall():
                live_ids.setdefault(row["doc_id"], set()).add(row["id"])
            allowed_ids = []
            for document in documents:
                if candidate and document["doc_id"] == candidate["doc_id"]:
                    continue
                values = document.get("chunk_ids") if source is stores[0] else [document.get("doc_db_id")]
                if isinstance(values, str):
                    values = json.loads(values)
                if not values or not all(isinstance(value, str) and value for value in values):
                    raise ValueError("文档缺少完整索引 ID 映射，请先重建该文档")
                available = live_ids.get(document["doc_id"], set())
                if (
                    not set(values).issubset(available)
                    and not document.get("active_generation")
                    and not document.get("index_generation")
                ):
                    # 无代次的旧索引允许保留其实际物理状态用于诊断；发布门禁另行拒绝。
                    capture_warnings.append(
                        {
                            "code": "legacy_registry_mapping_mismatch",
                            "doc_id": document["doc_id"],
                            "missing_ids": len(set(values) - available),
                        }
                    )
                    values = sorted(available)
                    if not values:
                        raise ValueError("旧索引文档没有可保留向量，请先修复该文档")
                allowed_ids.extend(values)
            cur.execute(
                f"""INSERT INTO {source._table}(id,collection,doc_id,content,metadata,embedding)
                SELECT %s||id,%s,doc_id,content,metadata,embedding FROM {source._table}
                WHERE ((collection=%s AND NOT(doc_id=ANY(%s)) AND id=ANY(%s)) OR collection=%s)
                AND metadata->>'kb_id'=%s AND doc_id=ANY(%s)""",
                (target + ":", target, source._collection, replaced, allowed_ids, candidate_source, kb_id, doc_ids),
            )
            if cur.rowcount == 0:
                raise ValueError("知识库的文档或片段向量为空，不能固定语料")
            cur.execute(f"SELECT id FROM {source._table} WHERE collection=%s", (source._collection,))
            found = {row["id"] for row in cur.fetchall()}
            if not set(allowed_ids).issubset(found):
                raise ValueError("发布索引尚未完成或存在缺失，稍后重试固定快照")
            cur.execute(
                f"""INSERT INTO {_meta_table_name()}(collection,embedding_provider,embedding_model,embedding_dimension,model_revision,index_version,status)
                SELECT %s,embedding_provider,embedding_model,embedding_dimension,model_revision,index_version,status
                FROM {_meta_table_name()} WHERE collection=%s""",
                (target, source._collection),
            )
            if cur.rowcount != 1:
                raise ValueError("原索引缺少模型元数据，不能创建可复现快照")
            manifests.append(vector_manifest(cur, source._table, target))
        fulltext_ids = {row.get("chunk_id") for row in fulltext if row.get("chunk_id")}
        cur.execute(f"SELECT metadata FROM {stores[0]._table} WHERE collection=%s", (names[0],))
        metadata_rows = [row["metadata"] for row in cur.fetchall()]
        available_parent_ids = fulltext_ids | {
            row.get("chunk_id") for row in metadata_rows if row.get("granularity") == "parent"
        }
        missing_parents = {
            row.get("parent_chunk_id") for row in metadata_rows if row.get("parent_chunk_id")
        } - available_parent_ids
        if missing_parents:
            affected = {row.get("doc_id") for row in metadata_rows if row.get("parent_chunk_id") in missing_parents}
            if candidate and candidate["doc_id"] in affected:
                raise ValueError(f"固定语料缺少 {len(missing_parents)} 个父块，拒绝不完整快照")
            capture_warnings.extend(
                {"code": "source_parent_mapping_missing", "doc_id": doc_id} for doc_id in sorted(affected)
            )
        record = {
            "snapshot_id": identity,
            "kb_id": kb_id,
            "actor": actor,
            "created_at": assets.now(),
            "collections": names,
            "documents": documents,
            "visible_ids": visible_ids,
            "capture_warnings": capture_warnings,
            "registry_vector_mapping_complete": not capture_warnings,
            "fulltext": fulltext,
            "vector_manifests": manifests,
        }
        # 统一转换日期类型，哈希与落盘内容一致。
        record = json.loads(json.dumps(record, default=str))
        record["content_hash"] = corpus_content_hash(record)
        # content_hash 用于跨 Run 比较；integrity_hash 保护包含随机快照标识的完整落盘记录。
        record["integrity_hash"] = assets.digest(record)
    # PG 提交成功后才公布；文件失败仅留下不可见的独立 collection，不污染正式索引。
    return assets.write_once("snapshots", identity, record)


def vector_manifest(cur, table: str, collection: str) -> str:
    cur.execute(
        f"SELECT id,md5(content||metadata::text||embedding::text) AS hash FROM {table} WHERE collection=%s ORDER BY id",
        (collection,),
    )
    prefix = f"{collection}:"
    rows = []
    for row in cur.fetchall():
        item = dict(row) if hasattr(row, "keys") else {"id": row[0], "hash": row[1]}
        vector_id = str(item["id"])
        if not vector_id.startswith(prefix):
            raise ValueError("固定语料向量 ID 不属于当前快照 collection")
        rows.append({"id": vector_id[len(prefix):], "hash": item["hash"]})
    return assets.digest(rows)


def load(identity: str) -> dict:
    record = assets.read("snapshots", identity)
    if record.get("integrity_hash"):
        payload = {k: v for k, v in record.items() if k != "integrity_hash"}
        integrity_valid = assets.digest(payload) == record["integrity_hash"]
        try:
            content_valid = corpus_content_hash(record) == record.get("content_hash")
        except (KeyError, TypeError):
            content_valid = False
    else:
        # 兼容旧快照：旧 content_hash 同时承担了内容指纹与整条记录校验。
        payload = {k: v for k, v in record.items() if k != "content_hash"}
        integrity_valid = assets.digest(payload) == record.get("content_hash")
        content_valid = integrity_valid
    if not integrity_valid or not content_valid:
        raise ValueError("语料快照内容校验失败")
    return record


class FrozenChunks:
    def __init__(self, record: dict):
        self.rows = record["fulltext"]

    def get_by_chunk_ids(self, ids):
        result = []
        for row in self.rows:
            if row.get("chunk_id") in ids:
                metadata = row.get("chunk_metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                result.append({**row, "chunk_metadata": metadata})
        return result

    def get_by_table_ids(self, doc_id, ids):
        result = []
        for row in self.rows:
            metadata = row.get("chunk_metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if row.get("doc_id") == doc_id and metadata.get("table_id") in ids:
                result.append({**row, "chunk_metadata": metadata})
        return result


def pipeline(record: dict):
    from psycopg2.extras import RealDictCursor

    from backend.config import BM25_CANDIDATE_K
    from backend.rag.embedding_singleton import get_embedding
    from backend.rag.pipeline import RAGPipeline
    from backend.rag.retrieval.bm25_store import BM25Store
    from backend.rag.vectorstore.pgvector_store import PgVectorKnowledgeStore

    instance = object.__new__(RAGPipeline)
    instance.mode = "evaluation"
    instance._person_to_doc_cache = {}
    instance.evaluation_bm25_dir = str(assets.root() / "snapshot-bm25" / record["snapshot_id"])
    instance.vectordb, instance.doc_db = [
        PgVectorKnowledgeStore(name, get_embedding()) for name in record["collections"]
    ]
    for store, expected in zip((instance.vectordb, instance.doc_db), record["vector_manifests"]):
        with store._conn() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if vector_manifest(cur, store._table, store._collection) != expected:
                raise ValueError("保留向量快照缺失或被修改，禁止回退当前索引")
    bm25 = BM25Store(instance.evaluation_bm25_dir)
    if bm25.load(k=BM25_CANDIDATE_K) is None:
        bm25.build(instance._build_bm25_corpus_from_vectorstore(), k=BM25_CANDIDATE_K)
    instance._init_retrievers()
    instance._evaluation_frozen = True
    return instance


@contextmanager
def activate(record):
    from backend.rag.indexing.chunk_store import read_store_override
    from backend.rag.retrieval.review_filter import visibility_snapshot

    if record is None:
        yield None
        return
    token = read_store_override.set(FrozenChunks(record))
    visibility_token = visibility_snapshot.set(frozenset(record["visible_ids"]))
    try:
        yield pipeline(record)
    finally:
        read_store_override.reset(token)
        visibility_snapshot.reset(visibility_token)
