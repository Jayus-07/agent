"""tests/selection_decision/test_decision_api.py — B1 拍板/表现回填 API 契约测试

对应 docs/2026-09-17-UX体验架构设计.md P0-③（拍板闭环）：
- 拍板 = record_decision 留痕 + set_user_decision 一步完成（workflow 不自动留痕）
- decision 非法值 400 且不留痕；任务不存在 404
- 表现回填 actual_metrics 幂等更新 + feedback_at 落库；decision 不存在 404
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.api.routes.selection_decision as route_mod
from backend.selection_decision.store import SelectionDecisionStore
from backend.tests.fixtures.pg_env import (  # noqa: F401
    pg_clean_tables,
    pg_iso_env,
)

TASK_INPUTS = {"category": "蓝牙耳机", "platforms": ["jd"]}


@pytest.fixture(autouse=True)
def _pg_iso(pg_clean_tables):
    """SQLite 轨删除：store 直连 PG，表走 pgtest_biz_ 前缀隔离。"""
    yield


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = SelectionDecisionStore(db_path=str(tmp_path / "sd_api.db"))
    monkeypatch.setattr(route_mod, "get_selection_decision_store", lambda: store)
    app = FastAPI()
    app.include_router(route_mod.router)
    return TestClient(app), store


def _create_task(store) -> str:
    return store.create(TASK_INPUTS)


def _decide(client, task_id, decision="adopted", **overrides):
    body = {
        "candidate_id": "jd:10001",
        "decision": decision,
        **overrides,
    }
    return client.post(f"/selection-decision/tasks/{task_id}/decisions", json=body)


class TestDecisionAPI:
    def test_decide_roundtrip(self, env):
        client, store = env
        task_id = _create_task(store)
        resp = _decide(client, task_id,
                       recommendation="recommend",
                       evidence_snapshot={"verdict": "sufficient"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["user_decision"] == "adopted"
        assert data["decision_version"] == 1
        assert data["recommendation"] == "recommend"
        assert data["evidence_snapshot"] == {"verdict": "sufficient"}
        assert data["task_id"] == task_id

        listed = client.get(f"/selection-decision/tasks/{task_id}/decisions").json()
        assert [d["decision_id"] for d in listed["decisions"]] == [data["decision_id"]]

    def test_decide_invalid_value_400_no_trace(self, env):
        client, store = env
        task_id = _create_task(store)
        resp = _decide(client, task_id, decision="maybe")
        assert resp.status_code == 400
        assert store.list_decisions_by_task(task_id) == []

    def test_decide_task_not_found_404(self, env):
        client, _ = env
        assert _decide(client, "no-such-task").status_code == 404
        resp = client.get("/selection-decision/tasks/no-such-task/decisions")
        assert resp.status_code == 404

    def test_version_increments_on_redecide(self, env):
        client, store = env
        task_id = _create_task(store)
        first = _decide(client, task_id).json()
        second = _decide(client, task_id, decision="rejected").json()
        assert second["decision_version"] == first["decision_version"] + 1
        assert second["user_decision"] == "rejected"
        assert len(store.list_decisions_by_task(task_id)) == 2

    def test_feedback_roundtrip_and_404(self, env):
        client, store = env
        task_id = _create_task(store)
        decision_id = _decide(client, task_id).json()["decision_id"]

        resp = client.post(
            f"/selection-decision/decisions/{decision_id}/feedback",
            json={"actual_metrics": {"sales_30d": 420, "rating": 4.6}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["actual_metrics"] == {"sales_30d": 420, "rating": 4.6}
        assert data["feedback_at"] is not None

        again = client.post(
            f"/selection-decision/decisions/{decision_id}/feedback",
            json={"actual_metrics": {"sales_30d": 500}},
        ).json()
        assert again["actual_metrics"] == {"sales_30d": 500}

        assert client.post(
            "/selection-decision/decisions/ghost/feedback",
            json={"actual_metrics": {}},
        ).status_code == 404


class TestStoreByTaskQuery:
    def test_list_decisions_by_task_isolated(self, tmp_path):
        store = SelectionDecisionStore(db_path=str(tmp_path / "sd.db"))
        store.create(TASK_INPUTS)
        store.record_decision(
            task_id="t1", candidate_id="c1", category="x",
            evidence_snapshot={}, score_snapshot={}, recommendation="r")
        store.record_decision(
            task_id="t2", candidate_id="c2", category="x",
            evidence_snapshot={}, score_snapshot={}, recommendation="r")
        assert [d["candidate_id"] for d in store.list_decisions_by_task("t1")] == ["c1"]
        assert store.list_decisions_by_task("missing") == []
