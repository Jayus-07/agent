"""#2 /agents 清单端点角色闸回归（2026-11-09）。

修复前：GET /agents 与 GET /agents/tools 无任何路由级身份依赖，任意已认证
主体（含 viewer JWT）可读取 Agent 图与 Tool 全量清单（含 source_file 源码路径）。

修复后：router 级挂 require_editor_user（对齐 admin_tools 的 tool_inventory）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app.api.routes import agents as agents_route


def _route_guards(router) -> dict:
    out = {}
    for r in router.routes:
        if not isinstance(r, APIRoute):
            continue
        guards = {
            d.call.__name__ for d in r.dependant.dependencies
            if getattr(d.call, "__name__", "").startswith("require_")
        }
        out[(r.path, tuple(sorted(r.methods)))] = guards
    return out


def test_agents_routes_require_editor():
    guards = _route_guards(agents_route.router)

    targets = [k for k in guards if k[0] in ("/agents", "/agents/tools")]
    assert len(targets) == 2, f"路由缺失: {sorted(guards)}"
    for key in targets:
        assert "require_editor_user" in guards[key], f"{key} 未挂 editor 闸: {guards[key]}"


def test_anonymous_request_is_rejected():
    """未认证不得 200；CI 无身份库时依赖 fail-closed 返回 503 也算拒绝。"""
    app = FastAPI()
    app.include_router(agents_route.router)
    res = TestClient(app).get("/agents")
    assert res.status_code >= 400, res.status_code
