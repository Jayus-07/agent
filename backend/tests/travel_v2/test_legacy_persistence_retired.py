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

    mounted = {
        (route.path, method)
        for route in api_router.routes
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
    if ("/travel/v2/trips", "POST") not in mounted:
        # 诊断（CI-only 空路由分诊 2026-11-10）：dump api_router 真实形态
        import sys

        _mods = sorted(
            k for k in sys.modules
            if k.endswith("api.router") or k.endswith("api\\router")
            or k == "api.router")
        _instances = {}
        for _k in _mods:
            _mod = sys.modules[_k]
            _ar = getattr(_mod, "api_router", None)
            _instances[_k] = (
                None if _ar is None
                else (len(getattr(_ar, "routes", [])), id(_ar)))
        _v2 = sys.modules.get("backend.travel_v2.api.router")
        _v2_len = None
        if _v2 is not None and hasattr(_v2, "router"):
            _v2_len = len(getattr(_v2.router, "routes", []))
        pytest.fail(
            f"travel/v2/trips 未挂载; mounted_count={len(mounted)}; "
            f"api_router_instances={_instances}; "
            f"travel_v2_router_routes={_v2_len}"
        )
    assert ("/travel/v2/trips", "POST") in mounted
    assert ("/travel/v2/plan/stream", "POST") in mounted
