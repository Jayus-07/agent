"""selection_decision API 测试（不真实跑 workflow：_run_task 打桩）"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.api.routes.selection_decision as sd_routes

VALID_PAYLOAD = {
    "category": "蓝牙耳机",
    "platforms": ["jd", "amazon"],
    "finance": {"sell_price": 129.0, "unit_cost": 45.0},
    "panel_size": 3,
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from backend.selection_decision.store import SelectionDecisionStore
    store = SelectionDecisionStore(db_path=str(tmp_path / "api.db"))
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)

    async def no_run(task_id, inputs):
        pass
    monkeypatch.setattr(sd_routes, "_run_task", no_run)

    app = FastAPI()
    app.include_router(sd_routes.router)
    return TestClient(app)


def test_post_task_creates_running_task(client):
    resp = client.post("/selection-decision/tasks", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_id"]


def test_post_task_validation(client):
    bad = {**VALID_PAYLOAD, "finance": {"sell_price": 0, "unit_cost": 45.0}}
    assert client.post("/selection-decision/tasks", json=bad).status_code == 422


def test_list_and_detail(client):
    task_id = client.post("/selection-decision/tasks", json=VALID_PAYLOAD).json()["task_id"]
    rows = client.get("/selection-decision/tasks").json()["tasks"]
    assert any(r["id"] == task_id for r in rows)
    detail = client.get(f"/selection-decision/tasks/{task_id}").json()
    assert detail["inputs"]["category"] == "蓝牙耳机"


def test_detail_404(client):
    assert client.get("/selection-decision/tasks/no-such").status_code == 404


def test_run_task_marks_failed_when_workflow_fails(monkeypatch, tmp_path):
    """workflow 返回 failed → store 行标记 failed + error"""
    from backend.selection_decision.store import SelectionDecisionStore
    store = SelectionDecisionStore(db_path=str(tmp_path / "rt.db"))
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)

    class _FakeCtx:
        status = "failed"
        error = "watchlist 为空"

    class _FakeExecutor:
        async def run(self, name, inputs=None):
            return _FakeCtx()

    monkeypatch.setattr(sd_routes, "WorkflowExecutor", lambda: _FakeExecutor())
    task_id = store.create({"category": "x"})
    asyncio.run(sd_routes._run_task(task_id, {"task_id": task_id}))
    row = store.get(task_id)
    assert row["status"] == "failed"
    assert "watchlist" in row["error"]


def test_run_task_marks_failed_on_exception(monkeypatch, tmp_path):
    """executor 抛异常 → store 行标记 failed + 异常信息"""
    from backend.selection_decision.store import SelectionDecisionStore
    store = SelectionDecisionStore(db_path=str(tmp_path / "rt2.db"))
    monkeypatch.setattr(sd_routes, "get_selection_decision_store", lambda: store)

    class _BoomExecutor:
        async def run(self, name, inputs=None):
            raise RuntimeError("executor 崩溃")

    monkeypatch.setattr(sd_routes, "WorkflowExecutor", lambda: _BoomExecutor())
    task_id = store.create({"category": "x"})
    asyncio.run(sd_routes._run_task(task_id, {"task_id": task_id}))
    row = store.get(task_id)
    assert row["status"] == "failed"
    assert "executor 崩溃" in row["error"]
