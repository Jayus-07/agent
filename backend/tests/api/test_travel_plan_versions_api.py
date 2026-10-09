"""tests/api/test_travel_plan_versions_api.py — 行程版本生命周期端点契约

契约（方案 v2 §5/§7）：/travel/plans/* 四端点全部要求已认证身份；service
层异常映射 404（不泄露存在性）/ 409（带 current_version）/ 422（语义非法）；
/plan 成功响应携带 plan_status 与 change_record 投影。

沿用 test_travel_auth.py 的最小 app 形态：只挂 travel router，隔离验证
router 层边界；service 一律打桩（业务逻辑见 tests/travel/test_plan_service.py）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import travel as travel_route
from backend.travel.core.plan_service import (
    PlanVersionConflict,
    PlanVersionNotFound,
    PlanVersionPersistenceError,
)

_AUTH = {"X-User-Id": "15", "X-User-Name": "Mint", "X-Auth-Type": "jwt"}

_CID_PATH = "/travel/plans/conv-1"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(travel_route.router)
    return TestClient(app, raise_server_exceptions=False)


def _patch_service(monkeypatch: pytest.MonkeyPatch, **methods) -> None:
    import backend.travel.core.plan_service as svc_mod

    class _Stub:
        pass

    for name, fn in methods.items():
        setattr(_Stub, name, staticmethod(fn))
    monkeypatch.setattr(svc_mod, "plan_version_service", _Stub())


# =============================================
# 鉴权边界：四个新端点未认证一律 401
# =============================================

_ENDPOINTS = [
    ("GET", "/travel/plans", None),
    ("GET", _CID_PATH + "/latest", None),
    ("GET", _CID_PATH + "/versions", None),
    ("POST", "/travel/plans/confirm",
     {"conversation_id": "conv-1", "plan_version": 2}),
    ("POST", "/travel/plans/discard",
     {"conversation_id": "conv-1", "plan_version": 2}),
    ("POST", "/travel/plans/restore",
     {"conversation_id": "conv-1", "target_version": 1, "base_version": 2}),
    ("GET", _CID_PATH + "/diff?from_version=1&to_version=2", None),
]


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_unauthenticated_is_401(method: str, path: str, body: dict | None) -> None:
    r = _client().request(method, path, json=body)
    assert r.status_code == 401, (
        f"{method} {path} 未认证应为 401，实得 {r.status_code}")


# =============================================
# 异常映射与响应结构（service 打桩）
# =============================================

def test_versions_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, list_versions=lambda cid, uid, *, tenant_id: [
        {"conversation_id": cid, "plan_version": 2, "plan_status": "waiting_confirmation",
         "destination": "福州", "change": {}, "created_at": "2026-10-01T00:00:00+00:00"},
    ])
    r = _client().get(_CID_PATH + "/versions", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["versions"][0]["plan_version"] == 2


# =============================================
# 历史规划：列表 + 会话最新版（恢复用）
# =============================================

def test_plan_list_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def _list(uid, *, limit=30, tenant_id):
        assert uid == "15"
        assert tenant_id
        return [{"conversation_id": "conv-1", "plan_version": 2,
                 "plan_status": "waiting_confirmation", "destination": "福州",
                 "created_at": "2026-10-01T00:00:00+00:00", "versions_count": 2}]
    _patch_service(monkeypatch, list_conversations=_list)
    r = _client().get("/travel/plans", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["plans"][0]["conversation_id"] == "conv-1"
    assert body["plans"][0]["versions_count"] == 2


def test_plan_latest_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def _latest(cid, uid, *, tenant_id):
        assert (cid, uid) == ("conv-1", "15")
        assert tenant_id
        return {"conversation_id": cid, "plan_version": 2,
                "plan_status": "waiting_confirmation", "destination": "福州",
                "created_at": "2026-10-01T00:00:00+00:00",
                "itinerary": {"plan_version": 2, "days": []}}
    def _active(cid, uid, *, tenant_id):
        assert tenant_id
        return {"plan_version": 1, "plan_status": "confirmed",
                "itinerary": {"plan_version": 1, "days": []}}
    _patch_service(monkeypatch, latest_version=_latest,
                   active_version=_active)
    r = _client().get(_CID_PATH + "/latest", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["plan_version"] == 2
    assert body["itinerary"]["plan_version"] == 2
    assert body["active_plan_version"] == 1
    assert body["active_itinerary"]["plan_version"] == 1


def test_plan_latest_missing_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch,
                   latest_version=lambda cid, uid, *, tenant_id: None)
    r = _client().get(_CID_PATH + "/latest", headers=_AUTH)
    assert r.status_code == 404


def test_confirm_conflict_carries_current_version(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def _confirm(cid, uid, plan_version, *, tenant_id):
        raise PlanVersionConflict("行程已更新到 v3，请确认当前版本",
                                  current_version=3)
    _patch_service(monkeypatch, confirm=_confirm)
    r = _client().post("/travel/plans/confirm",
                       json={"conversation_id": "conv-1", "plan_version": 2},
                       headers=_AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["current_version"] == 3


def test_confirm_not_found_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def _confirm(cid, uid, plan_version, *, tenant_id):
        raise PlanVersionNotFound("无可用行程版本")
    _patch_service(monkeypatch, confirm=_confirm)
    r = _client().post("/travel/plans/confirm",
                       json={"conversation_id": "conv-1", "plan_version": 1},
                       headers=_AUTH)
    assert r.status_code == 404


def test_discard_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def _discard(cid, uid, plan_version, *, tenant_id):
        assert (cid, uid, plan_version) == ("conv-1", "15", 2)
        assert tenant_id
        return {"status": "ok", "plan_version": 2,
                "plan_status": "discarded"}
    _patch_service(monkeypatch, discard=_discard)
    r = _client().post("/travel/plans/discard",
                       json={"conversation_id": "conv-1", "plan_version": 2},
                       headers=_AUTH)
    assert r.status_code == 200
    assert r.json()["plan_status"] == "discarded"


def test_discard_conflict_maps_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    def _discard(cid, uid, plan_version, *, tenant_id):
        raise PlanVersionConflict("草案已不是当前版本", current_version=3)
    _patch_service(monkeypatch, discard=_discard)
    r = _client().post("/travel/plans/discard",
                       json={"conversation_id": "conv-1", "plan_version": 2},
                       headers=_AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["current_version"] == 3


def test_restore_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def _restore(cid, uid, *, target_version, base_version, tenant_id):
        assert (target_version, base_version) == (1, 3)
        return {"status": "ok", "itinerary": {"plan_version": 4},
                "plan_status": "waiting_confirmation",
                "change_record": {"change": {"type": "rollback"}}}
    _patch_service(monkeypatch, restore=_restore)
    r = _client().post("/travel/plans/restore",
                       json={"conversation_id": "conv-1",
                             "target_version": 1, "base_version": 3},
                       headers=_AUTH)
    assert r.status_code == 200
    assert r.json()["itinerary"]["plan_version"] == 4


def test_diff_not_found_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def _diff(cid, uid, *, from_version, to_version, tenant_id):
        raise PlanVersionNotFound("版本不存在或不在保留窗口内")
    _patch_service(monkeypatch, diff=_diff)
    r = _client().get(_CID_PATH + "/diff?from_version=1&to_version=9",
                      headers=_AUTH)
    assert r.status_code == 404


def test_restore_invalid_body_is_422() -> None:
    r = _client().post("/travel/plans/restore",
                       json={"conversation_id": "conv-1"},  # 缺版本字段
                       headers=_AUTH)
    assert r.status_code == 422


# =============================================
# /plan 集成：成功响应携带版本投影
# =============================================

def test_plan_response_carries_plan_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """域图成功出单 → 响应含 plan_status / change_record（service 打桩验证投影接线）。"""
    class _FakeGraph:
        def invoke(self, _input, config=None):
            return {
                "final_answer": "行程单",
                "brief": {"destination": "福州", "days": 1},
                "itinerary": {"plan_version": 1, "days": []},
                "candidates": [{"poi_id": "p1"}],
                "validation": None,
                "clarifications": [],
                "travel_context": {},
            }

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph",
                        lambda: _FakeGraph())

    import backend.travel.core.plan_service as svc_mod
    monkeypatch.setattr(
        svc_mod, "plan_version_service",
        type("_Stub", (), {
            "latest_version": staticmethod(
                lambda cid, uid, *, tenant_id, strict=False: None),
            "record_plan_result": staticmethod(
                lambda cid, uid, itin, *, tenant_id: {
                    "plan_status": "confirmed",
                    "change_record": {"parent_version": 0},
                })
        })())

    r = _client().post("/travel/plan", json={"message": "福州1天", "conversation_id": "conv-1"},
                       headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["plan_status"] == "confirmed"
    assert body["change_record"] == {"parent_version": 0}


def test_plan_ledger_failure_never_returns_generated_itinerary_as_success(
        monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeGraph:
        def invoke(self, _input, config=None):
            return {
                "final_answer": "生成行程",
                "brief": {"destination": "福州", "days": 1},
                "itinerary": {"plan_version": 1, "days": []},
                "candidates": [{"poi_id": "p1"}],
                "validation": None,
                "clarifications": [],
                "travel_context": {},
            }

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph",
                        lambda: _FakeGraph())

    def _fail(cid, uid, itinerary, *, tenant_id):
        raise PlanVersionPersistenceError("账本不可用")

    import backend.travel.core.plan_service as svc_mod
    monkeypatch.setattr(
        svc_mod, "plan_version_service",
        type("_Stub", (), {
            "latest_version": staticmethod(
                lambda cid, uid, *, tenant_id, strict=False: None),
            "record_plan_result": staticmethod(_fail),
        })())
    r = _client().post(
        "/travel/plan",
        json={"message": "福州1天", "conversation_id": "conv-1"},
        headers=_AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["itinerary"] is None
    assert body["error_type"] == "plan_version_persistence_failed"


def test_plan_without_itinerary_has_no_plan_status(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """追问/无数据轮没有行程 → 不出现 plan_status 投影（前端按缺省渲染）。"""
    class _FakeGraph:
        def invoke(self, _input, config=None):
            return {
                "final_answer": "请告诉我几天",
                "brief": {"destination": "福州", "days": 0},
                "candidates": [],
                "clarifications": ["几天？"],
                "travel_context": {},
            }

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph",
                        lambda: _FakeGraph())
    r = _client().post("/travel/plan", json={"message": "福州"},
                       headers=_AUTH)
    assert r.status_code == 200
    assert "plan_status" not in r.json()


def test_answer_with_carried_itinerary_does_not_create_a_version(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.travel.core.plan_service as svc_mod

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("问答轮不能写入行程版本")

    monkeypatch.setattr(
        svc_mod, "plan_version_service",
        type("_Stub", (), {"record_plan_result": staticmethod(_unexpected)})())
    answer = {"status": "answered", "itinerary": {"plan_version": 4}}

    assert travel_route._record_plan_version(
        answer, "conv-1", "15", "tenant-a") == answer
