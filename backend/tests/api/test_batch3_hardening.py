"""批三加固回归（审计 #9 / #20 / #33，2026-11-09）。"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request


def _req(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": raw, "query_string": b""})


# ── #33：可选网关信任标记 ──────────────────────────────────────


def test_trust_secret_rejects_direct_headers(monkeypatch):
    from backend.config import auth as auth_cfg
    from backend.app.api import identity as identity_mod

    monkeypatch.setattr(auth_cfg, "GATEWAY_TRUST_SECRET", "shared-secret")
    ident = identity_mod._from_headers(_req({
        auth_cfg.USER_ID_HEADER: "42",
        auth_cfg.TENANT_ID_HEADER: "tenant-a",
        auth_cfg.AUTH_TYPE_HEADER: "jwt",
    }))
    assert ident.authenticated is False
    assert ident.source == "untrusted"


def test_trust_secret_accepts_gateway_marked_request(monkeypatch):
    from backend.config import auth as auth_cfg
    from backend.app.api import identity as identity_mod

    monkeypatch.setattr(auth_cfg, "GATEWAY_TRUST_SECRET", "shared-secret")
    ident = identity_mod._from_headers(_req({
        auth_cfg.USER_ID_HEADER: "42",
        auth_cfg.TENANT_ID_HEADER: "tenant-a",
        auth_cfg.AUTH_TYPE_HEADER: "jwt",
        auth_cfg.GATEWAY_TRUST_HEADER: "shared-secret",
    }))
    assert ident.authenticated is True
    assert ident.tenant_id == "tenant-a"


def test_trust_secret_absent_keeps_old_behavior(monkeypatch):
    from backend.config import auth as auth_cfg
    from backend.app.api import identity as identity_mod

    monkeypatch.setattr(auth_cfg, "GATEWAY_TRUST_SECRET", "")
    ident = identity_mod._from_headers(_req({
        auth_cfg.USER_ID_HEADER: "42",
        auth_cfg.TENANT_ID_HEADER: "tenant-a",
        auth_cfg.AUTH_TYPE_HEADER: "jwt",
    }))
    assert ident.authenticated is True


# ── #9：replay 端点管理端闸 ──────────────────────────────────


def test_replay_route_requires_admin():
    from backend.app.api.routes import observability

    found = None
    for r in observability.router.routes:
        if isinstance(r, APIRoute) and r.path.endswith("/traces/{trace_id}/replay"):
            found = r
            break
    assert found is not None, "replay 路由缺失"
    guards = {
        d.call.__name__ for d in found.dependant.dependencies
        if getattr(d.call, "__name__", "").startswith("_require_")
    }
    assert "_require_observability_admin" in guards, guards


# ── #20：SLA 事件封套带租户 ──────────────────────────────────


def test_sla_event_kwargs_carries_tenant():
    from backend.customer_service.maintenance import _sla_event_kwargs

    payload = _sla_event_kwargs({
        "handoff_id": "h-1", "conversation_id": "c-1",
        "tenant_id": "tenant-a", "level": "warning",
        "elapsed_seconds": 120,
    })
    assert payload["tenant_id"] == "tenant-a"
    assert payload["conversation_id"] == "c-1"
