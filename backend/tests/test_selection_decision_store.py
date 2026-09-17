"""selection_decision store 测试（PG pgtest_biz_ 前缀隔离）"""
import os
from datetime import datetime, timedelta

import psycopg2
import pytest

from backend.config.database import SELECTION_DECISION_PG_CONFIG
from backend.selection_decision.store import SelectionDecisionStore
from backend.tests.fixtures.pg_env import (  # noqa: F401
    pg_clean_tables,
    pg_iso_env,
)


@pytest.fixture(autouse=True)
def _pg_iso(pg_clean_tables):
    """SQLite 轨删除：store 直连 PG，表走 pgtest_biz_ 前缀隔离。"""
    yield


@pytest.fixture
def store(tmp_path):
    return SelectionDecisionStore(db_path=str(tmp_path / "sd.db"))


def test_create_returns_id_and_running_status(store):
    task_id = store.create({"category": "蓝牙耳机"})
    row = store.get(task_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["inputs"]["category"] == "蓝牙耳机"


def test_update_result(store):
    task_id = store.create({})
    store.update_result(task_id, status="success", verdict="go",
                        report_md="# 报告", trace_id="tr-1")
    row = store.get(task_id)
    assert row["status"] == "success"
    assert row["verdict"] == "go"
    assert row["report_md"] == "# 报告"
    assert row["finished_at"] is not None


def test_list_orders_by_created_desc(store):
    a = store.create({"n": 1})
    b = store.create({"n": 2})
    rows = store.list()
    assert [r["id"] for r in rows][:2] == [b, a]
    assert "report_md" not in rows[0]  # 列表不返回大字段


def test_get_missing_returns_none(store):
    assert store.get("no-such-id") is None


def test_ensure_task_creates_missing_row(store):
    store.ensure_task("t-x")
    row = store.get("t-x")
    assert row is not None
    assert row["status"] == "running"


def test_ensure_task_keeps_existing_row(store):
    tid = store.create({"a": 1})
    store.ensure_task(tid, {"b": 2})
    assert store.get(tid)["inputs"] == {"a": 1}


def test_mark_stale_running_failed(store):
    """超龄 running 行（服务重启中断）标记 failed，新 running 行不动"""
    stale_id = store.create({"n": "stale"})
    fresh_id = store.create({"n": "fresh"})
    # 把 stale 行的 created_at 改成 10 分钟前（ISO 字符串可直接字典序比较）
    old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    table = os.getenv("SELECTION_DECISION_PG_TABLE_PREFIX", "") + "selection_tasks"
    conn = psycopg2.connect(**SELECTION_DECISION_PG_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET created_at = %s WHERE id = %s",
                (old, stale_id),
            )
        conn.commit()
    finally:
        conn.close()

    n = store.mark_stale_running_failed(300)
    assert n == 1
    stale = store.get(stale_id)
    assert stale["status"] == "failed"
    assert "服务重启" in stale["error"]
    assert stale["finished_at"] is not None
    fresh = store.get(fresh_id)
    assert fresh["status"] == "running"
