"""selection_decision API 测试（不真实跑 workflow：_run_task 打桩）"""
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
