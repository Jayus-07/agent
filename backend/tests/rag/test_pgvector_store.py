"""test_pgvector_store.py — PgVectorKnowledgeStore 测试。

两部分:
  1. where→SQL 翻译器纯单测（无外部依赖，全分支）
  2. 真 PG 集成冒烟（本地 PG 可达时运行；VECTOR_PG_TABLE_PREFIX=test_ 隔离，
     结束后 drop 清理；embedding 用确定性 stub，不触网）

运行: .venv/Scripts/python.exe -m pytest backend/tests/rag/test_pgvector_store.py -v
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

from backend.rag.vectorstore.knowledge_store import normalize_where
from backend.rag.vectorstore.pgvector_store import (
    EMBEDDING_DIM,
    PgVectorKnowledgeStore,
    _scalar_to_text,
    where_to_sql,
)


# ======================= 翻译器纯单测 =======================

class TestScalarToText:
    def test_bool(self):
        assert _scalar_to_text(True) == "true"
        assert _scalar_to_text(False) == "false"

    def test_numbers(self):
        assert _scalar_to_text(1) == "1"
        assert _scalar_to_text(1.0) == "1.0"

    def test_none(self):
        assert _scalar_to_text(None) == ""


class TestWhereToSql:
    def test_none_and_empty(self):
        assert where_to_sql(None, []) == ""
        assert where_to_sql({}, []) == ""

    def test_flat_scalar_eq(self):
        params: list = []
        sql = where_to_sql({"kb_id": "kb-a"}, params)
        assert sql == "metadata @> %s::jsonb"
        assert params == ['{"kb_id": "kb-a"}']

    def test_flat_multi_key_and(self):
        params: list = []
        sql = where_to_sql({"kb_id": "a", "doc_type": "b"}, params)
        assert sql.startswith("(") and " AND " in sql
        assert len(params) == 2

    def test_normalized_and_form(self):
        params: list = []
        where = normalize_where({"kb_id": "a", "doc_type": "b"})
        sql = where_to_sql(where, params)
        assert sql.startswith("(") and " AND " in sql
        assert len(params) == 2

    def test_or_group(self):
        params: list = []
        sql = where_to_sql({"$or": [{"kb_id": "a"}, {"kb_id": "b"}]}, params)
        assert " OR " in sql and len(params) == 2

    def test_in_operator(self):
        params: list = []
        sql = where_to_sql({"kb_id": {"$in": ["a", "b"]}}, params)
        assert sql == "metadata->>'kb_id' = ANY(%s)"
        assert params == [["a", "b"]]

    def test_nin_operator(self):
        params: list = []
        sql = where_to_sql({"kb_id": {"$nin": ["a"]}}, params)
        assert "NOT (metadata->>'kb_id' = ANY(%s))" == sql

    def test_numeric_comparison(self):
        params: list = []
        sql = where_to_sql({"confidence": {"$gt": 0.5}}, params)
        assert sql == "(metadata->>'confidence')::numeric > %s"
        assert params == [0.5]

    def test_string_comparison(self):
        params: list = []
        sql = where_to_sql({"name": {"$gte": "abc"}}, params)
        assert sql == "metadata->>'name' >= %s"
        assert params == ["abc"]

    def test_ne_operator(self):
        params: list = []
        sql = where_to_sql({"kb_id": {"$ne": "x"}}, params)
        assert sql == "NOT (metadata @> %s::jsonb)"

    def test_contains(self):
        params: list = []
        sql = where_to_sql({"$contains": "退款"}, params)
        assert sql == "content ILIKE %s"
        assert params == ["%退款%"]

    def test_nested_and_or(self):
        params: list = []
        sql = where_to_sql(
            {"$and": [{"kb_id": "a"}, {"$or": [{"dept": "x"}, {"dept": "y"}]}]}, params)
        assert "AND" in sql and "OR" in sql
        assert len(params) == 3

    def test_bool_eq_jsonb_form(self):
        params: list = []
        where_to_sql({"is_latest": True}, params)
        assert params == ['{"is_latest": true}']

    def test_unknown_op_raises(self):
        with pytest.raises(ValueError):
            where_to_sql({"k": {"$weird": 1}}, [])


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
    """确定性伪嵌入：文本 hash → 固定维度向量（不触网、可复现）。"""

    def __init__(self, dim: int = EMBEDDING_DIM):
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


@pytest.fixture()
def store(tmp_path):
    if not _pg_available():
        pytest.skip("本地 PG 不可达，跳过 pgvector 集成冒烟")
    os.environ["VECTOR_PG_TABLE_PREFIX"] = "test_"
    s = PgVectorKnowledgeStore(
        persist_directory=str(tmp_path / "chroma_it"),
        embedding_function=_StubEmbedding(),
    )
    from backend.config.database import VECTOR_PG_CONFIG
    import psycopg2
    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    conn.autocommit = True
    # setup：清残留（前次中断可能留下数据）；注意不能 DROP 表——
    # store 的 _DDL_DONE 进程级缓存假设表在进程生命周期内持续存在
    conn.cursor().execute(f"DELETE FROM {s._table} WHERE collection = %s", (s._collection,))
    conn.close()
    yield s
    conn = psycopg2.connect(**VECTOR_PG_CONFIG)
    conn.autocommit = True
    conn.cursor().execute(f"DELETE FROM {s._table} WHERE collection = %s", (s._collection,))
    conn.close()
    os.environ.pop("VECTOR_PG_TABLE_PREFIX", None)


@pytest.mark.usefixtures("store")
class TestPgVectorStoreIntegration:
    def test_roundtrip(self, store: PgVectorKnowledgeStore):
        docs = [
            _Doc("退款政策：7 天无理由退款", {"doc_id": "d1", "chunk_index": 0, "kb_id": "kb-a"}),
            _Doc("发货时效：48 小时内发货", {"doc_id": "d1", "chunk_index": 1, "kb_id": "kb-a"}),
            _Doc("会员权益说明", {"doc_id": "d2", "chunk_index": 0, "kb_id": "kb-b"}),
        ]
        ids = store.add_documents(docs)
        assert len(ids) == 3 and all(i.startswith("chroma_it:d") for i in ids)
        assert store.count() == 3

        # 幂等重放：同批再写 → 行数不变
        store.add_documents(docs)
        assert store.count() == 3

        # 语义检索（stub 向量下同文本最近）
        hits = store.similarity_search("退款政策：7 天无理由退款", k=2)
        assert len(hits) == 2
        assert hits[0].page_content.startswith("退款政策")

        # with_score：距离 ∈ [0, 2]（cosine distance 量纲）
        scored = store.similarity_search_with_score("发货时效", k=1, filter={"kb_id": "kb-a"})
        assert len(scored) == 1
        assert 0.0 <= scored[0][1] <= 2.0

        # metadata 过滤（$in）
        docs_b = store.similarity_search("会员权益说明", k=5, filter={"kb_id": {"$in": ["kb-b"]}})
        assert len(docs_b) == 1 and docs_b[0].metadata["doc_id"] == "d2"

        # $or 过滤
        or_docs = store.similarity_search(
            "会员权益说明", k=5, filter={"$or": [{"kb_id": "kb-a"}, {"kb_id": "kb-b"}]})
        assert len(or_docs) == 3

        # get
        got = store.get(where={"doc_id": "d1"})
        assert len(got["ids"]) == 2
        assert set(got.keys()) >= {"ids", "metadatas", "documents"}

        # update_metadata_where
        n = store.update_metadata_where({"doc_id": "d1"}, {"is_latest": False})
        assert n == 2
        got2 = store.get(where={"is_latest": False})
        assert len(got2["ids"]) == 2

        # delete by ids（精确计数）
        n = store.delete(ids=ids[:2])
        assert n == 2 and store.count() == 1

        # delete by where（精确计数，Chroma 无此能力）
        rest = store.get()
        n = store.delete(where={"doc_id": "d2"})
        assert n == 1 and store.count() == 0

    def test_upsert_texts_and_deterministic_ids(self, store: PgVectorKnowledgeStore):
        store.upsert_texts(
            ["快照 A", "快照 B"],
            [{"snapshot_id": 1}, {"snapshot_id": 2}],
            ["snap-1", "snap-2"],
        )
        assert store.count() == 2
        # 同 ID 重写 → 覆盖不新增
        store.upsert_texts(["快照 A 更新"], [{"snapshot_id": 1}], ["snap-1"])
        assert store.count() == 2
        hits = store.similarity_search("快照 A 更新", k=1)
        assert hits[0].page_content == "快照 A 更新"

        # add_texts 无 metadata → md5 确定性 ID
        ids1 = store.add_texts(["自由文本"], [{}])
        ids2 = store.add_texts(["自由文本"], [{}])
        assert ids1 == ids2 and store.count() == 3
