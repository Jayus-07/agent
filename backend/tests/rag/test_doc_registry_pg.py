"""test_doc_registry_pg.py — R1/C19：doc_registry PostgreSQL 连接层测试。

覆盖：
  1. 引擎分发（DOC_REGISTRY_BACKEND=postgres → PG 子类；默认 sqlite 不受影响）
  2. 全接口行为往返（register/upsert/查询/状态机/版本/过期）
  3. 多进程并发批量入库无锁冲突验收（spawn 4 进程 × 25 文档 + 单行争写）

前置：本机 PostgreSQL 可达（backend/config/database.py::DOC_REGISTRY_PG_CONFIG，
默认 agent_memory 库）。不可达时整文件 skip；unit-only 运行用 -m "not pg"。
隔离：使用专用测试表（env DOC_REGISTRY_PG_TABLE=doc_registry_r1_test），跑前跑后清空。
"""
from __future__ import annotations

import multiprocessing as mp
import os

import psycopg2
import pytest

from backend.config.database import DOC_REGISTRY_PG_CONFIG
from backend.rag.indexing.doc_registry import DOC_STATUSES, DocumentRegistry

PG_TABLE = "doc_registry_r1_test"


def _pg_alive() -> bool:
    try:
        conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not _pg_alive(),
        reason="No PostgreSQL reachable (DOC_REGISTRY_PG_CONFIG)",
    ),
]


@pytest.fixture()
def pg_reg(monkeypatch):
    """PG 模式 + 专用测试表的 registry（每个用例独立清空）。"""
    monkeypatch.setenv("DOC_REGISTRY_BACKEND", "postgres")
    monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
    reg = DocumentRegistry("ignored/path.db")
    reg.clear()
    yield reg
    reg.clear()


# ── 并发 worker（模块级，spawn 可 pickle）──────────────────────────────

def _pg_conc_worker(worker_id: int, docs_per_worker: int, contention_rounds: int) -> int:
    """单 worker：批量注册独占行 + 与其他 worker 争写同一行（upsert 冲突）。"""
    from backend.rag.indexing.doc_registry import DocumentRegistry as Reg
    reg = Reg(f"ignored/worker_{worker_id}")
    for i in range(docs_per_worker):
        reg.register(
            f"test://conc/w{worker_id}_d{i}.md", f"conc_w{worker_id}_d{i}",
            f"hash_{worker_id}_{i}", "kb_conc",
            [f"conc_w{worker_id}_d{i}_0", f"conc_w{worker_id}_d{i}_1"],
            f"db_{worker_id}_{i}",
            metadata={"doc_type": "faq", "confidence": 0.5 + i / 100},
        )
    for r in range(contention_rounds):
        # 同一行（SQLAlchemy 式 hot row）并发 upsert — SQLite 单写者下这里是锁冲突点
        reg.register(
            "test://conc/hot_row.md", "conc_hot", f"hash_hot_{worker_id}_{r}",
            "kb_conc", [f"hot_{worker_id}_{r}"], "db_hot",
            metadata={"doc_type": "general"},
        )
    return docs_per_worker + contention_rounds


# ── 1. 引擎分发 ──────────────────────────────────────────────────────

class TestDispatch:
    def test_env_postgres_returns_pg_impl(self, monkeypatch):
        monkeypatch.setenv("DOC_REGISTRY_BACKEND", "postgres")
        monkeypatch.setenv("DOC_REGISTRY_PG_TABLE", PG_TABLE)
        from backend.rag.indexing.doc_registry_pg import PostgresDocumentRegistry
        reg = DocumentRegistry("any/path.db")
        assert isinstance(reg, PostgresDocumentRegistry)
        assert isinstance(reg, DocumentRegistry)  # isinstance 兼容

    def test_default_stays_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOC_REGISTRY_BACKEND", raising=False)
        reg = DocumentRegistry(str(tmp_path / "reg.db"))
        assert type(reg) is DocumentRegistry  # 非 PG 子类

    def test_unknown_value_falls_back_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DOC_REGISTRY_BACKEND", "whatever")
        reg = DocumentRegistry(str(tmp_path / "reg.db"))
        assert type(reg) is DocumentRegistry

    def test_status_enum_shared(self):
        assert "pending_review" in DOC_STATUSES and "failed" in DOC_STATUSES


# ── 2. 接口行为往返 ──────────────────────────────────────────────────

class TestInterface:
    def test_register_and_get_by_path(self, pg_reg):
        pg_reg.register(
            "test://a/doc.md", "doc_a", "hash_a", "kb1",
            ["doc_a_0", "doc_a_1"], "dbid_a",
            metadata={"doc_type": "faq", "confidence": 0.9, "llm_used": True,
                      "summary": "s", "keywords": "k", "department": "legal",
                      "doc_version": 3, "kb_version": "v2"},
        )
        row = pg_reg.get_by_path("test://a/doc.md")
        assert row is not None
        assert row["doc_id"] == "doc_a" and row["file_hash"] == "hash_a"
        assert row["chunk_count"] == 2 and row["status"] == "active"
        assert row["llm_used"] == 1 and abs(row["confidence"] - 0.9) < 1e-9
        assert row["doc_version"] == 3 and row["department"] == "legal"
        # chunk_ids 往返（TEXT JSON）
        import json
        assert json.loads(row["chunk_ids"]) == ["doc_a_0", "doc_a_1"]

    def test_upsert_overwrites_same_path(self, pg_reg):
        pg_reg.register("test://a/doc.md", "doc_a", "hash_v1", "kb1", ["c0"], "d1")
        pg_reg.register("test://a/doc.md", "doc_a", "hash_v2", "kb1", ["c0", "c1"], "d2")
        row = pg_reg.get_by_path("test://a/doc.md")
        assert row["file_hash"] == "hash_v2" and row["chunk_count"] == 2

    def test_get_by_path_none_when_missing(self, pg_reg):
        assert pg_reg.get_by_path("test://missing.md") is None

    def test_get_by_doc_id_active_priority(self, pg_reg):
        pg_reg.register("test://x1.md", "dup1", "h1", "kb1", [], "d")
        pg_reg.mark_deleted_by_doc_id("dup1")
        pg_reg.register("test://x2.md", "dup1", "h2", "kb1", [], "d")
        row = pg_reg.get_by_doc_id("dup1")
        assert row["status"] == "active" and row["file_path"] == "test://x2.md"

    def test_register_in_progress_creates_parsing_row(self, pg_reg):
        pg_reg.register_in_progress("test://prog.md", "prog1", "hp", "kb1")
        row = pg_reg.get_by_path("test://prog.md")
        assert row["status"] == "parsing" and row["file_hash"] == "hp"
        # 已有行：保留 chunk 元数据只改状态
        pg_reg.update_status_by_doc_id("prog1", "active")
        pg_reg.register("test://prog.md", "prog1", "hp2", "kb1", ["p0"], "dp")
        pg_reg.register_in_progress("test://prog.md", "prog1", "hp3", "kb1")
        row2 = pg_reg.get_by_path("test://prog.md")
        assert row2["status"] == "parsing" and row2["chunk_count"] == 1

    def test_search_keyword_case_insensitive(self, pg_reg):
        pg_reg.register("test://s/ReportFAQ.md", "s1", "h", "kb1", [], "d",
                        metadata={"doc_type": "faq"})
        result = pg_reg.search(keyword="reportfaq")  # SQLite LIKE 对 ASCII 不区分大小写
        assert result["total"] == 1 and result["items"][0]["doc_id"] == "s1"
        by_type = pg_reg.search(doc_type="faq", kb_id="kb1")
        assert by_type["total"] == 1

    def test_search_pagination(self, pg_reg):
        for i in range(5):
            pg_reg.register(f"test://p/doc_{i}.md", f"p{i}", "h", "kbp", [], "d")
        page1 = pg_reg.search(kb_id="kbp", page=1, page_size=2)
        page2 = pg_reg.search(kb_id="kbp", page=2, page_size=2)
        assert page1["total"] == 5 and len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        paths1 = {it["file_path"] for it in page1["items"]}
        paths2 = {it["file_path"] for it in page2["items"]}
        assert not (paths1 & paths2)  # 无重复

    def test_invalid_status_raises(self, pg_reg):
        with pytest.raises(ValueError):
            pg_reg.update_status("test://a.md", "bogus")
        with pytest.raises(ValueError):
            pg_reg.update_status_by_doc_id("d", "bogus")

    def test_list_helpers(self, pg_reg):
        pg_reg.register("test://l1.md", "l1", "h", "kbL", [], "d",
                        metadata={"doc_type": "pdf"})
        pg_reg.register("test://l2.md", "l2", "h", "kbL2", [], "d",
                        metadata={"doc_type": "md"})
        assert {r["doc_id"] for r in pg_reg.list_by_kb("kbL")} == {"l1"}
        assert {r["doc_id"] for r in pg_reg.list_by_doc_type("pdf")} == {"l1"}
        assert pg_reg.count() == 2
        assert pg_reg.count_by_kb_id("kbL") == 1
        assert pg_reg.list_all()["test://l1.md"]["doc_id"] == "l1"
        assert {r["doc_id"] for r in pg_reg.list_by_statuses(("active",))} == {"l1", "l2"}

    def test_version_and_delete_ops(self, pg_reg):
        pg_reg.register("test://v.md", "v1", "h", "kb1", [], "d")
        assert pg_reg.bump_doc_version("v1") == 2
        assert pg_reg.bump_doc_version("v1") == 3
        assert pg_reg.bump_doc_version("missing") == -1
        assert pg_reg.mark_deleted("test://v.md") is None  # 无返回值，不抛错即可
        assert pg_reg.get_by_path("test://v.md")["status"] == "deleted"

    def test_expire_lifecycle(self, pg_reg):
        pg_reg.register("test://e.md", "e1", "h", "kb1", [], "d")
        pg_reg.ensure_expire_at_column()  # 幂等
        assert pg_reg.set_expire_at("e1", "2020-01-01") == 1
        expired = pg_reg.list_expired()
        assert any(r["doc_id"] == "e1" for r in expired)
        assert pg_reg.archive_expired() == 1
        assert pg_reg.get_by_doc_id("e1")["status"] == "deleted"
        assert pg_reg.archive_expired() == 0

    def test_update_after_reindex(self, pg_reg):
        pg_reg.register("test://r.md", "r1", "h_old", "kb1", ["old0"], "d_old")
        pg_reg.update_after_reindex("test://r.md", "h_new", ["new0", "new1"], "d_new")
        row = pg_reg.get_by_path("test://r.md")
        assert row["file_hash"] == "h_new" and row["doc_db_id"] == "d_new"
        assert row["chunk_count"] == 2 and row["status"] == "active"


# ── 3. 多进程并发批量入库（R1 验收：无锁冲突）────────────────────────

class TestConcurrentWrites:
    def test_multiprocess_bulk_register_no_lock_conflict(self, pg_reg):
        """4 进程并发写：独占行 + 热行争写，全部成功且计数精确。"""
        n_workers, docs_per, hot_rounds = 4, 25, 10
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            results = pool.starmap(
                _pg_conc_worker,
                [(w, docs_per, hot_rounds) for w in range(n_workers)],
            )
        assert results == [docs_per + hot_rounds] * n_workers

        expected = n_workers * docs_per + 1  # 独占行 + 1 条热行
        assert pg_reg.count() == expected
        assert pg_reg.count_by_kb_id("kb_conc") == expected
        hot = pg_reg.get_by_path("test://conc/hot_row.md")
        assert hot is not None and hot["status"] == "active"
        # 热行最终态 = 某个 worker 的最后一次写入（版本号一致）
        import json
        assert json.loads(hot["chunk_ids"])[0].startswith("hot_")
        # 分进程独占行逐条校验
        for w in range(n_workers):
            for i in range(docs_per):
                row = pg_reg.get_by_path(f"test://conc/w{w}_d{i}.md")
                assert row is not None and row["doc_id"] == f"conc_w{w}_d{i}"
