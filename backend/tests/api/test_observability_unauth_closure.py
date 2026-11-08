"""可观测性 / capabilities / llm 端点的鉴权依赖收口（2026-11-09 审计）。

此前 /observability/rag-traces*、/tokens/*、/graph、/gateway-access-logs、
/system-health 以及 /capabilities、/llm/models|balance 无后端鉴权。
本测试直接检查路由依赖（不依赖 DB/Redis），锁住 router 级 require_user_actor：
删除或绕过依赖会让测试失败。
"""
from __future__ import annotations

import pytest

from backend.app.api.routes import capabilities, llm, observability


def _dep_names(router) -> dict[str, set[str]]:
    return {
        route.path: {
            getattr(dep.call, "__name__", "")
            for dep in route.dependant.dependencies
        }
        for route in router.routes
    }


@pytest.mark.parametrize(
    "path",
    [
        "/observability/rag-traces",
        "/observability/rag-traces/stream",
        "/observability/rag-traces/{trace_id}",
        "/observability/tokens/summary",
        "/observability/graph",
        "/observability/gateway-access-logs",
        "/observability/system-health",
        "/observability/traces/{trace_id}/replay",
    ],
)
def test_observability_routes_require_identity(path):
    deps = _dep_names(observability.router)
    assert path in deps, f"{path} 路由不存在"
    assert "_require_trace_reader" in deps[path], (
        f"{path} 缺少 router 级 require_user_actor 守卫"
    )


def test_capabilities_router_requires_identity():
    deps = _dep_names(capabilities.router)
    assert deps, "capabilities 路由为空"
    for path, names in deps.items():
        assert "require_user_actor" in names, f"{path} 缺少身份守卫"


def test_llm_router_requires_identity():
    deps = _dep_names(llm.router)
    assert deps, "llm 路由为空"
    for path, names in deps.items():
        assert "require_user_actor" in names, f"{path} 缺少身份守卫"
