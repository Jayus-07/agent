"""tests/api/test_travel_candidates_api.py — 分类候选表端点契约（验收 #10）

契约：/travel/candidates 要求已认证身份；权限对齐 plans（账本查无此人
404 不泄露存在性）；候选池读域图 checkpoint（get_state），不可达时
available=false + 提示、不伪造；分组「景点/美食/酒店」+ rating 降序 +
每组截 12 条；plan_version 随响应带出（前端候选表版本标记，#102 口径）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import travel as travel_route

_AUTH = {"X-User-Id": "15", "X-User-Name": "Mint", "X-Auth-Type": "jwt"}
_PATH = "/travel/candidates?conversation_id=conv-1"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(travel_route.router)
    return TestClient(app, raise_server_exceptions=False)


def _patch_service(monkeypatch: pytest.MonkeyPatch, latest=None) -> None:
    import backend.travel.core.plan_service as svc_mod

    class _Stub:
        pass

    _Stub.latest_version = staticmethod(
        lambda cid, uid, *, tenant_id: latest)
    monkeypatch.setattr(svc_mod, "plan_version_service", _Stub())


def _patch_graph(monkeypatch: pytest.MonkeyPatch, candidates=None,
                 error: bool = False) -> list[tuple]:
    """打桩域图单例的 get_state；返回 calls 供断言 thread_id。"""
    import backend.travel.graph_builder as gb

    calls: list[tuple] = []

    class _Snap:
        def __init__(self, values):
            self.values = values

    class _Graph:
        def get_state(self, config):
            calls.append((config["configurable"]["thread_id"],))
            if error:
                raise RuntimeError("checkpoint unavailable")
            return _Snap({"candidates": candidates})

    monkeypatch.setattr(gb, "get_travel_graph", lambda: _Graph())
    return calls


_LATEST = {"conversation_id": "conv-1", "plan_version": 3,
           "plan_status": "ready", "destination": "福州",
           "created_at": "2026-10-05T00:00:00+00:00", "itinerary": {}}


def _cand(poi_id, name, category, rating, reason="高分优先", source="amap:live"):
    return {"poi_id": poi_id, "name": name, "category": category,
            "rating": rating, "reason": reason, "source": source,
            "lat": 26.08, "lng": 119.29}


# =============================================
# 鉴权与权限
# =============================================


def test_unauthenticated_is_401():
    assert _client().get(_PATH).status_code == 401


def test_no_plan_record_is_404(monkeypatch: pytest.MonkeyPatch):
    """账本查无此人（不存在/越权同形）→ 404，不触发 checkpoint 读取。"""
    calls = _patch_graph(monkeypatch, candidates=[])
    _patch_service(monkeypatch, latest=None)
    r = _client().get(_PATH, headers=_AUTH)
    assert r.status_code == 404
    assert calls == []  # 越权面不触达候选池


# =============================================
# 正常分组
# =============================================


def test_grouped_ok_with_plan_version(monkeypatch: pytest.MonkeyPatch):
    _patch_service(monkeypatch, latest=_LATEST)
    _patch_graph(monkeypatch, candidates=[
        _cand("p1", "三坊七巷", "景点", 4.8),
        _cand("p2", "西湖公园", "公园", 4.6),      # 细分类并入景点大组
        _cand("p3", "同利肉燕", "美食", 4.7),
        _cand("p4", "低分店", "美食", 3.9),
    ])
    r = _client().get(_PATH, headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["plan_version"] == 3
    assert body["available"] is True
    assert body["hint"] == ""
    names = [x["name"] for x in body["groups"]["景点"]]
    assert names == ["三坊七巷", "西湖公园"]  # rating 降序
    assert [x["name"] for x in body["groups"]["美食"]] == ["同利肉燕", "低分店"]
    assert body["groups"]["酒店"] == []  # 无生产者 → 空组（前端隐藏）
    item = body["groups"]["景点"][0]
    assert set(item) == {"poi_id", "name", "category", "rating", "reason", "source"}


def test_group_cap_twelve(monkeypatch: pytest.MonkeyPatch):
    """每组截前 12 条（池上限 120，防 payload 过大）。"""
    _patch_service(monkeypatch, latest=_LATEST)
    _patch_graph(monkeypatch, candidates=[
        _cand(f"p{i}", f"景点{i}", "景点", 4.0) for i in range(20)
    ])
    body = _client().get(_PATH, headers=_AUTH).json()
    assert len(body["groups"]["景点"]) == 12


# =============================================
# checkpoint 不可达：不伪造
# =============================================


def test_checkpoint_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch):
    """checkpoint 读取异常 → available=false + 空分组 + 提示（不伪造）。"""
    _patch_service(monkeypatch, latest=_LATEST)
    _patch_graph(monkeypatch, error=True)
    r = _client().get(_PATH, headers=_AUTH)
    assert r.status_code == 200  # 增强展示降级，不是错误
    body = r.json()
    assert body["available"] is False
    assert body["groups"] == {"景点": [], "美食": [], "酒店": []}
    assert "候选池暂不可用" in body["hint"]


def test_empty_candidates_returns_unavailable(monkeypatch: pytest.MonkeyPatch):
    """checkpoint 活着但候选池空（异常轮）→ 如实 unavailable。"""
    _patch_service(monkeypatch, latest=_LATEST)
    _patch_graph(monkeypatch, candidates=[])
    body = _client().get(_PATH, headers=_AUTH).json()
    assert body["available"] is False
    assert body["hint"]


def test_thread_id_is_namespaced_like_plan_chain(monkeypatch: pytest.MonkeyPatch):
    """get_state 的 thread_id 必须与规划链路同源（travel:{tenant}:{user}:{conv}）。

    实测（2026-10-05）：checkpoint 键是复合 namespace（STOP C 跨租户隔离），
    裸 conversation_id 读不到 —— 本用例锁定单一事实源口径。
    """
    _patch_service(monkeypatch, latest=_LATEST)
    calls = _patch_graph(monkeypatch, candidates=[_cand("p1", "x", "景点", 4.0)])
    _client().get(_PATH, headers=_AUTH)
    assert calls and calls[0][0] == "travel:default:15:conv-1"
