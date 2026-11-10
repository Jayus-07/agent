"""competitor 端点角色闸回归（审计 P1-5，2026-11-03）。

修复前：/competitor/* 除 test-cookies 外全部无鉴权依赖——任何 viewer 可
覆盖爬虫账号 Cookie（写库立即生效）、增删监控项、触发全量外呼扫描。

修复后分档（依赖注入，网关粗闸不在此测）：
  - admin 档：cookies 读/写/删、qr-login start/poll、test-cookies（凭据与
    外呼副作用面，与既有 test-cookies=admin 一致）
  - editor 档：watchlist 读写、history/stats/anti-ban/qr-platforms（运营面）
  - 公开读：GET /recommendations（与 /selection/recommendations 等价的
    业务读，有意不挂闸——锁死防止误加闸锁死用户端选品推荐）

灰度语义锁死：SENSITIVE_API_GUARD_MODE=audit 下低角色放行（前端 viewer
用户流不被打破）；enforce 下 403。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import competitor as competitor_route
from backend.app.api import deps as deps_mod


# ── 轻量 app：只挂 competitor router，不挂全局中间件 ──

@pytest.fixture
def app(monkeypatch):
    from backend.services import sys_config

    # 默认 enforce：角色闸真实拒绝；audit 用例单独覆写
    monkeypatch.setattr(deps_mod, "get_mode", lambda _name: "enforce")
    # 审批门/幂等链不在本测试面（写端点的行为由既有契约测试覆盖），
    # 这里把审批 helper 桩为直接执行回调，聚焦角色闸本身
    monkeypatch.setattr(
        competitor_route, "_run_idempotent_write",
        lambda _req, _op, _payload, callback, **_kw: callback(),
    )
    return FastAPI()


@pytest.fixture
def client(app):
    app.include_router(competitor_route.router)
    return TestClient(app, raise_server_exceptions=False)


def _as(role: str):
    async def _resolve(request):  # resolve_operator_role 是 async 依赖
        return OperatorIdentity(role=role, actor=f"user:u-{role}")
    return _resolve


@pytest.fixture
def set_identity(monkeypatch):
    def _set(role: str):
        monkeypatch.setattr(deps_mod, "resolve_operator_role", _as(role))
    return _set


# ── 一、结构断言：每个端点挂了正确档位的守卫 ──

_EDITOR = set()
_ADMIN = set()
for _p, _m in [
    ("/competitor/watchlist", "GET"), ("/competitor/watchlist", "POST"),
    ("/competitor/watchlist", "DELETE"), ("/competitor/watchlist", "PATCH"),
    ("/competitor/history", "GET"), ("/competitor/analyze", "POST"),
    ("/competitor/scan", "POST"), ("/competitor/stats", "GET"),
    ("/competitor/qr-login/platforms", "GET"),
    ("/competitor/retry-blocked", "POST"),
    ("/competitor/anti-ban/stats", "GET"),
    ("/competitor/anti-ban/resume", "POST"),
]:
    _EDITOR.add((_p, _m))
for _p, _m in [
    ("/competitor/cookies", "GET"), ("/competitor/cookies", "POST"),
    ("/competitor/cookies", "DELETE"), ("/competitor/test-cookies", "POST"),
    ("/competitor/qr-login/start", "POST"),
    ("/competitor/qr-login/poll", "POST"),
]:
    _ADMIN.add((_p, _m))
_PUBLIC = {("/competitor/recommendations", "GET")}


def _route_guard_names(app):
    """path+method → 本轮守卫依赖的函数名集合（无守卫 = 空集）。

    fastapi 0.161（requirements-lock）起 include_router 惰性挂载：
    app.routes 里是 _IncludedRouter 包装对象而非展平的 APIRoute
    （0.136 才展平）——必须沿 original_router 递归解包，否则守卫集
    恒为空（周回归 v4/v5 CI-only 失败根因，2026-11-10 实证）。
    """
    from fastapi.routing import APIRoute

    def _iter(routes):
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
                continue
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from _iter(getattr(nested, "routes", []))

    out = {}
    for r in _iter(app.routes):
        guards = {
            d.call.__name__ for d in r.dependant.dependencies
            if getattr(d.call, "__name__", "").startswith("require_")
        }
        for m in r.methods:
            if m in ("GET", "POST", "DELETE", "PATCH"):
                out[(r.path, m)] = guards
    return out


def test_all_guarded_endpoints_carry_expected_tier(app):
    app.include_router(competitor_route.router)
    guards = _route_guard_names(app)

    for key in _EDITOR | _ADMIN | _PUBLIC:
        assert key in guards, f"路由缺失: {key}"
    for key in _EDITOR:
        assert guards[key] == {"require_editor_user"}, f"{key} 应挂 editor 闸，实际 {guards[key]}"
    for key in _ADMIN:
        assert guards[key] == {"require_admin_user"}, f"{key} 应挂 admin 闸，实际 {guards[key]}"
    for key in _PUBLIC:
        assert guards[key] == set(), f"{key} 应保持公开业务读，实际 {guards[key]}"


# ── 二、行为断言：enforce 拒绝 / audit 灰度放行 ──

def test_editor_read_ok_and_viewer_denied_under_enforce(
        client, set_identity, monkeypatch):
    from backend.competitor.store import get_store as _real_store

    class _FakeStore:
        def list_watch(self, enabled_only=True):
            return []

        def latest_snapshot(self, url):
            return None

        def history(self, url, limit=50):
            return []

        def get_watch_by_url(self, url):
            return None

    monkeypatch.setattr(competitor_route, "get_store", lambda: _FakeStore())

    set_identity("editor")
    assert client.get("/competitor/stats").status_code == 200

    set_identity("viewer")
    resp = client.get("/competitor/stats")
    assert resp.status_code == 403


def test_admin_tier_cookie_read_denies_editor_under_enforce(
        client, set_identity, monkeypatch):
    monkeypatch.setattr(
        competitor_route, "cookie_manager",
        SimpleNamespace(list_cookies=lambda: []),
    )

    set_identity("admin")
    assert client.get("/competitor/cookies").status_code == 200

    set_identity("editor")
    assert client.get("/competitor/cookies").status_code == 403

    set_identity("viewer")
    assert client.get("/competitor/cookies").status_code == 403


def test_audit_mode_keeps_viewer_flow_alive(client, set_identity, monkeypatch):
    """灰度语义锁死：audit 模式下 viewer 放行（前端用户流不因本闸打破）。"""
    monkeypatch.setattr(deps_mod, "get_mode", lambda _name: "audit")

    class _FakeStore:
        def list_watch(self, enabled_only=True):
            return []

        def latest_snapshot(self, url):
            return None

        def history(self, url, limit=50):
            return []

        def get_watch_by_url(self, url):
            return None

    monkeypatch.setattr(competitor_route, "get_store", lambda: _FakeStore())

    set_identity("viewer")
    assert client.get("/competitor/stats").status_code == 200


def test_recommendations_stays_public_without_identity(client, monkeypatch):
    """无身份也不得 403/401：业务公开读（与 /selection/recommendations 同口径）。"""
    import sys
    import types

    fake_sel = types.ModuleType("backend.selection.recommender")
    fake_sel.recommend = lambda **kw: {"items": [], "total": 0}
    monkeypatch.setitem(sys.modules, "backend.selection.recommender", fake_sel)

    # 不设置任何身份（resolve_operator_role 保持未桩也不可走真链——桩 guest）
    monkeypatch.setattr(
        deps_mod, "resolve_operator_role", _as("viewer"))
    resp = client.get("/competitor/recommendations")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}
