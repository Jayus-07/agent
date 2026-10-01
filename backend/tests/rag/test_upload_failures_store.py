"""入库失败待处理信号（rag_index_runs 派生口径）测试。

口径：failed 且同 file_path 尚无更新的 published 运行才计数——重传成功
自动消数（不加状态列，G2 派生量）；每文件取最新一次失败。
表用专用测试表隔离（跑前跑后 DROP）。
"""
from __future__ import annotations

import psycopg2
import pytest

from backend.config.database import DOC_REGISTRY_PG_CONFIG
from backend.rag.indexing.index_run_store_pg import IndexRunStore

TEST_TABLE = "rag_index_runs_uf_test"


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


def _exec(sql: str, args: tuple = ()) -> None:
    conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG)
    conn.autocommit = True
    try:
        conn.cursor().execute(sql, args)
    finally:
        conn.close()


@pytest.fixture()
def store():
    _exec(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    s = IndexRunStore(TEST_TABLE)
    yield s
    _exec(f"DROP TABLE IF EXISTS {TEST_TABLE}")


def _add(store: IndexRunStore, upload_id: str, file_path: str, status: str, *,
         created_at: str, error: str = "", department: str = "finance") -> None:
    store.create_run(upload_id=upload_id, generation=f"g-{upload_id}",
                     file_path=file_path, department=department)
    _exec(f"UPDATE {TEST_TABLE} SET status=%s, error=%s, created_at=%s "
          f"WHERE upload_id=%s", (status, error, created_at, upload_id))


def test_failed_counts_and_lists(store):
    _add(store, "u1", "/d/a.pdf", "failed",
         created_at="2026-10-01 10:00:00", error="质量门禁硬异常: cleaned_chars(8 < 20)")
    assert store.count_pending_failures() == 1
    items = store.list_pending_failures()
    assert len(items) == 1
    assert items[0]["upload_id"] == "u1"
    assert "质量门禁" in items[0]["error"]
    assert items[0]["department"] == "finance"


def test_republish_supersedes_failure(store):
    """同文件重传成功（更新的 published）→ 失败自动消数。"""
    _add(store, "u1", "/d/a.pdf", "failed", created_at="2026-10-01 10:00:00", error="x")
    _add(store, "u2", "/d/a.pdf", "published", created_at="2026-10-01 11:00:00")
    assert store.count_pending_failures() == 0
    assert store.list_pending_failures() == []


def test_failure_after_publish_still_pending(store):
    """成功在先、失败在后 → 仍待处理（最新状态是失败）。"""
    _add(store, "u1", "/d/a.pdf", "published", created_at="2026-10-01 10:00:00")
    _add(store, "u2", "/d/a.pdf", "failed",
         created_at="2026-10-01 11:00:00", error="produced 0 chunks")
    assert store.count_pending_failures() == 1
    items = store.list_pending_failures()
    assert items[0]["upload_id"] == "u2"


def test_latest_failure_per_file(store):
    """同文件多次失败只计一条，取最新。"""
    _add(store, "u1", "/d/a.pdf", "failed", created_at="2026-10-01 10:00:00", error="old")
    _add(store, "u2", "/d/a.pdf", "failed", created_at="2026-10-01 12:00:00", error="new")
    assert store.count_pending_failures() == 1
    items = store.list_pending_failures()
    assert len(items) == 1 and items[0]["error"] == "new"


def test_independent_files_and_order(store):
    """不同文件互不影响；清单新失败在前。"""
    _add(store, "u1", "/d/a.pdf", "failed", created_at="2026-10-01 10:00:00", error="e1")
    _add(store, "u2", "/d/b.docx", "failed", created_at="2026-10-01 11:00:00", error="e2")
    _add(store, "u3", "/d/c.md", "published", created_at="2026-10-01 09:00:00")
    assert store.count_pending_failures() == 2
    items = store.list_pending_failures()
    assert [i["upload_id"] for i in items] == ["u2", "u1"]
