"""V1 旅游规划存储不能在启动或请求时自动复活。"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("module_path", "store_name"),
    [
        ("backend.travel.core.plan_store", "plan_store"),
        ("backend.travel.core.decision_store", "decision_store"),
        ("backend.tools.travel.preferences", "preferences"),
    ],
)
def test_legacy_storage_does_not_create_retired_tables(
    monkeypatch, module_path: str, store_name: str
):
    module = __import__(module_path, fromlist=[store_name])
    monkeypatch.setattr(module, "_initialized", False)

    calls = 0

    def unexpected_connection():
        nonlocal calls
        calls += 1
        raise AssertionError("退役存储不得连接数据库或自动建表")

    monkeypatch.setattr(module, "_conn", unexpected_connection)

    assert module._ensure_table() is False
    assert calls == 0


def test_legacy_trip_persistence_api_routes_are_not_mounted():
    from backend.app.api.router import api_router

    # fastapi 0.161（requirements-lock）起 include_router 惰性挂载：
    # routes 里是 _IncludedRouter 包装对象，须沿 original_router 递归
    # 解包才能看到真实 path/methods（周回归 CI-only 失败根因，
    # 2026-11-10 实证；与 test_cs_dispatch.iter_routes 同款）。
    def _iter_routes(routes):
        for route in routes:
            if getattr(route, "path", None) is not None:
                yield route
            nested = getattr(route, "original_router", None)
            if nested is not None:
                yield from _iter_routes(getattr(nested, "routes", []))

    mounted = {
        (route.path, method)
        for route in _iter_routes(api_router.routes)
        for method in getattr(route, "methods", ())
    }

    retired = {
        ("/travel/plan", "POST"),
        ("/travel/plan/stream", "POST"),
        ("/travel/preferences", "GET"),
        ("/travel/preferences", "PUT"),
        ("/travel/plans", "GET"),
        ("/travel/plans/{conversation_id}", "DELETE"),
        ("/travel/plans/{conversation_id}/latest", "GET"),
        ("/travel/plans/{conversation_id}/versions", "GET"),
        ("/travel/candidates", "GET"),
        ("/travel/decisions", "GET"),
        ("/travel/decisions", "POST"),
        ("/travel/templates", "GET"),
    }

    assert mounted.isdisjoint(retired)
    assert ("/travel/v2/trips", "POST") in mounted
    assert ("/travel/v2/plan/stream", "POST") in mounted
