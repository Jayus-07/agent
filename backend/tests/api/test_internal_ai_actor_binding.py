"""审计 #38：内部工具网关的委托 actor 必须是真实启用用户，并绑定其租户。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security.task_authorization import (
    TaskAuthorizationDenied,
    resolve_delegated_actor,
)


def _fake_user(monkeypatch, row):
    from backend.security import task_authorization as ta
    monkeypatch.setattr(ta, "_fetch_auth_user", lambda _uid: row)


def test_resolve_rejects_non_numeric(monkeypatch):
    with pytest.raises(TaskAuthorizationDenied):
        resolve_delegated_actor("whatsapp-user-1")


def test_resolve_rejects_missing_and_disabled(monkeypatch):
    _fake_user(monkeypatch, None)
    with pytest.raises(TaskAuthorizationDenied):
        resolve_delegated_actor("42")
    _fake_user(monkeypatch, ("viewer", 0, "dept", "tenant-a"))
    with pytest.raises(TaskAuthorizationDenied):
        resolve_delegated_actor("42")


def test_resolve_rejects_empty_tenant(monkeypatch):
    _fake_user(monkeypatch, ("viewer", 1, "dept", ""))
    with pytest.raises(TaskAuthorizationDenied):
        resolve_delegated_actor("42")


def test_resolve_returns_tenant_and_roles(monkeypatch):
    _fake_user(monkeypatch, ("editor", 1, "dept-a", "tenant-a"))
    assert resolve_delegated_actor("42") == ("tenant-a", "dept-a", ("editor",))


def _route_client(monkeypatch, actor_resolver):
    from backend.app.api.routes import internal_ai
    import backend.core.request_context as rc
    from backend.security import task_authorization as ta
    from mcp_servers import manager as mgr

    recorded: dict = {}
    for name in ("set_tool_user_id", "set_tool_department",
                 "set_tool_tenant_id", "set_tool_roles", "set_tool_permissions"):
        monkeypatch.setattr(
            rc, name,
            (lambda n: lambda v: recorded.__setitem__(n, v))(name),
        )
    monkeypatch.setattr(ta, "resolve_delegated_actor", actor_resolver)
    monkeypatch.setattr(internal_ai, "_ensure_gateway_enabled", lambda: None)
    monkeypatch.setattr(internal_ai, "_allowed_tools", lambda: "*")
    monkeypatch.setattr(
        mgr, "route",
        lambda tool, params: {"ok": True, "server": "rag", "result": {}},
    )
    app = FastAPI()
    app.include_router(internal_ai.router)
    app.dependency_overrides[internal_ai.require_internal_token] = lambda: None
    return TestClient(app, raise_server_exceptions=False), recorded


def test_call_binds_actor_tenant(monkeypatch):
    client, recorded = _route_client(
        monkeypatch, lambda uid: ("tenant-a", "dept-a", ("editor",)))
    resp = client.post("/internal/ai/call", json={
        "tool": "search_knowledge", "params": {}, "actor_user_id": "42",
    })
    assert resp.status_code == 200, resp.text
    assert recorded["set_tool_user_id"] == "42"
    assert recorded["set_tool_tenant_id"] == "tenant-a"
    assert recorded["set_tool_roles"] == ("editor",)


def test_call_rejects_untrusted_actor(monkeypatch):
    def _deny(_uid):
        raise TaskAuthorizationDenied("用户已禁用")

    client, _ = _route_client(monkeypatch, _deny)
    resp = client.post("/internal/ai/call", json={
        "tool": "search_knowledge", "params": {}, "actor_user_id": "42",
    })
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "ActorNotTrusted"
