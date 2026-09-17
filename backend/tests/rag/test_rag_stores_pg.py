"""test_rag_stores_pg.py — 迁移计划 2026-09-17 Batch B：RAG 族 PG 连接层测试。

覆盖：
  1. 工厂分发（CHUNK_STORE_BACKEND / KEYWORD_STORE_BACKEND / OPLOG_BACKEND =
     postgres → PG 子类；默认 sqlite 不受影响）
  2. PostgresChunkStore：insert_batch/get_by_doc_id 往返（simulated_questions
     JSON 反序列化）、delete_by_doc_id、count_by_doc_id
  3. PostgresKeywordRuleStore：首启种子导入、get_active 缓存结构、upsert/toggle/
     delete、list_all 过滤
  4. PostgresDocumentOperationLogger：log/list 过滤分页、get_last_ops_batch

前置：本机 PostgreSQL 可达（backend/config/database.py::RAG_STORES_PG_CONFIG，
默认 agent_memory 库）。不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：专用测试表前缀（env RAG_STORES_PG_TABLE_PREFIX=pgtest_rag_），fixture 清空。
"""
from __future__ import annotations

import time

import psycopg2
import pytest

from backend.config.database import RAG_STORES_PG_CONFIG

PG_PREFIX = "pgtest_rag_"


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**RAG_STORES_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not _pg_alive(),
        reason="No PostgreSQL reachable (RAG_STORES_PG_CONFIG)",
    ),
]

_TABLES = [f"{PG_PREFIX}chunk_store", f"{PG_PREFIX}keyword_rules",
           f"{PG_PREFIX}doc_operation_log"]


def _drop_tables():
    conn = psycopg2.connect(**RAG_STORES_PG_CONFIG)
    try:
        cur = conn.cursor()
        for t in _TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def rag_env(monkeypatch):
    """PG 模式 + 专用测试表前缀。"""
    monkeypatch.setenv("CHUNK_STORE_BACKEND", "postgres")
    monkeypatch.setenv("KEYWORD_STORE_BACKEND", "postgres")
    monkeypatch.setenv("OPLOG_BACKEND", "postgres")
    monkeypatch.setenv("RAG_STORES_PG_TABLE_PREFIX", PG_PREFIX)
    # 重置工厂单例（缓存实例不会因删表而重建，见 Batch A 测试注释）
    import backend.rag.indexing.chunk_store as cs_mod
    import backend.rag.preprocessing.keyword_store as ks_mod
    import backend.app.api.routes._rag_shared as shared_mod
    monkeypatch.setattr(cs_mod, "_store", None)
    monkeypatch.setattr(ks_mod, "_store", None)
    monkeypatch.setattr(shared_mod, "_op_logger", None)
    yield monkeypatch


@pytest.fixture()
def clean_tables(rag_env):
    _drop_tables()
    yield
    _drop_tables()


# ── 工厂分发 ─────────────────────────────────────────────────────────

class TestFactoryDispatch:
    def test_default_is_sqlite(self, monkeypatch):
        import backend.rag.indexing.chunk_store as cs_mod
        import backend.rag.preprocessing.keyword_store as ks_mod
        monkeypatch.setattr(cs_mod, "_store", None)
        monkeypatch.setattr(ks_mod, "_store", None)
        monkeypatch.delenv("CHUNK_STORE_BACKEND", raising=False)
        monkeypatch.delenv("KEYWORD_STORE_BACKEND", raising=False)
        assert type(cs_mod.get_chunk_store()) is cs_mod.ChunkStore
        assert type(ks_mod.get_keyword_store()) is ks_mod.KeywordRuleStore

    def test_postgres_dispatch(self, clean_tables):
        import backend.rag.indexing.chunk_store as cs_mod
        import backend.rag.preprocessing.keyword_store as ks_mod
        from backend.rag.indexing.chunk_store_pg import PostgresChunkStore
        from backend.rag.preprocessing.keyword_store_pg import PostgresKeywordRuleStore

        assert isinstance(cs_mod.get_chunk_store(), PostgresChunkStore)
        assert isinstance(ks_mod.get_keyword_store(), PostgresKeywordRuleStore)


# ── PostgresChunkStore ───────────────────────────────────────────────

class TestChunkStorePG:
    def test_insert_get_roundtrip(self, clean_tables):
        from backend.rag.indexing.chunk_store import get_chunk_store
        store = get_chunk_store()
        n = store.insert_batch("doc-pg-1", [
            {"chunk_index": 0, "content": "第一段", "keywords": "退货,物流",
             "section_title": "第一章", "simulated_questions": ["怎么退货？"]},
            {"chunk_index": 1, "content": "第二段"},
        ])
        assert n == 2
        rows = store.get_by_doc_id("doc-pg-1")
        assert len(rows) == 2
        assert rows[0]["content"] == "第一段"
        assert rows[0]["simulated_questions"] == ["怎么退货？"]
        assert rows[1]["simulated_questions"] == []
        assert store.count_by_doc_id("doc-pg-1") == 2

    def test_delete_by_doc_id(self, clean_tables):
        from backend.rag.indexing.chunk_store import get_chunk_store
        store = get_chunk_store()
        store.insert_batch("doc-pg-2", [{"chunk_index": 0, "content": "x"}])
        assert store.delete_by_doc_id("doc-pg-2") == 1
        assert store.get_by_doc_id("doc-pg-2") == []


# ── PostgresKeywordRuleStore ─────────────────────────────────────────

class TestKeywordStorePG:
    def test_seed_and_active(self, clean_tables):
        from backend.rag.preprocessing.keyword_store import get_keyword_store
        store = get_keyword_store()
        active = store.get_active()
        assert len(active["keywords"]) > 0          # 种子已导入
        assert "by_doc_type_w" in active
        rules = store.get_rules_by_doc_type()
        assert isinstance(rules, dict) and len(rules) > 0

    def test_upsert_toggle_delete(self, clean_tables):
        from backend.rag.preprocessing.keyword_store import get_keyword_store
        store = get_keyword_store()
        store.upsert("测试词PG", doc_type="faq", category="测试分类", weight=3)
        rows = store.list_all(search="测试词PG")
        assert len(rows) == 1
        assert rows[0]["doc_type"] == "faq"
        assert rows[0]["source"] == "manual"

        store.toggle("测试词PG", enabled=0)
        assert store.list_all(search="测试词PG", enabled=0)[0]["enabled"] == 0

        assert store.delete("测试词PG")["ok"] is True
        assert store.list_all(search="测试词PG") == []

    def test_list_doc_types_and_categories(self, clean_tables):
        from backend.rag.preprocessing.keyword_store import get_keyword_store
        store = get_keyword_store()
        types = store.list_doc_types()
        assert "faq" in types and "general" in types
        assert isinstance(store.list_categories(), list)


# ── PostgresDocumentOperationLogger ──────────────────────────────────

class TestOpLogPG:
    def test_log_and_list(self, clean_tables):
        from backend.app.api.routes._rag_shared import _get_op_logger
        logger = _get_op_logger()
        logger.log(doc_id="d1", doc_name="a.md", operation="upload",
                   source="test", trace_id="tr-1", result="success",
                   detail={"chunk_count": 3}, duration_ms=120)
        logger.log(doc_id="d1", doc_name="a.md", operation="delete", source="test")
        res = logger.list(page=1, page_size=10)
        assert res["total"] == 2
        res = logger.list(operation="upload")
        assert res["total"] == 1 and res["items"][0]["trace_id"] == "tr-1"

    def test_get_last_ops_batch(self, clean_tables):
        from backend.app.api.routes._rag_shared import _get_op_logger
        logger = _get_op_logger()
        logger.log(doc_id="d2", doc_name="b.md", operation="upload", trace_id="tr-a")
        time.sleep(0.01)
        logger.log(doc_id="d2", doc_name="b.md", operation="reindex", trace_id="tr-b")
        last_ops, last_traces = logger.get_last_ops_batch(["d2"])
        assert last_ops["d2"]["operation"] == "reindex"
        assert last_traces["d2"] == "tr-b"
        assert logger.get_last_ops_batch([]) == ({}, {})

    def test_invalid_operation_rejected(self, clean_tables):
        from backend.app.api.routes._rag_shared import _get_op_logger
        logger = _get_op_logger()
        with pytest.raises(ValueError):
            logger.log(doc_id="d", doc_name="x", operation="bogus")
