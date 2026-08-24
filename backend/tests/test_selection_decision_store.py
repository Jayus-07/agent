"""selection_decision store 测试（tmp_path 隔离）"""
import pytest

from backend.selection_decision.store import SelectionDecisionStore


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
