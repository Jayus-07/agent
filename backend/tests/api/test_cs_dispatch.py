"""P4 用户转人工入池路由测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.identity import Identity
from backend.app.api.routes import cs_dispatch
from backend.customer_service.dispatch.service import (
    ConversationForbidden,
    ConversationNotFound,
    HandoffResult,
)
from backend.memory.database import MemoryDatabaseUnavailable

PATH = "/cs/conversations/conv-1/handoff"


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(cs_dispatch.router)

    async def fake_session():
        yield object()

    app.dependency_overrides[cs_dispatch.get_session] = fake_session
    return app


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _identity(*, user_id: str = "user-1", tenant_id: str = "tenant-a") -> Identity:
    return Identity(
        user_id=user_id,
        tenant_id=tenant_id,
        auth_type="jwt",
        source="header",
    )


def _install_identity(app: FastAPI, identity: Identity) -> None:
    app.dependency_overrides[cs_dispatch.require_identity] = lambda: identity


def _result(*, reused: bool) -> HandoffResult:
    return HandoffResult(
        handoff_id="HANDOFF-1",
        conversation_id="conv-1",
        handoff_state="waiting_human",
        total_deadline_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        reused=reused,
    )


def test_unauthed_request_returns_401_without_service_call(client, monkeypatch):
    called = False

    async def fake_service(**_kwargs):
        nonlocal called
        called = True
        return _result(reused=False)

    monkeypatch.setattr(cs_dispatch, "create_or_reuse_handoff", fake_service)

    response = client.post(PATH, headers={"Idempotency-Key": "request-1"})

    assert response.status_code == 401
    assert called is False


def test_missing_or_blank_idempotency_key_returns_400(client, app):
    _install_identity(app, _identity())

    missing = client.post(PATH)
    blank = client.post(PATH, headers={"Idempotency-Key": "   "})

    assert missing.status_code == 400
    assert blank.status_code == 400


def test_idempotency_key_over_128_bytes_returns_400(client, app):
    _install_identity(app, _identity())

    response = client.post(PATH, headers={"Idempotency-Key": "k" * 129})

    assert response.status_code == 400


def test_missing_trusted_tenant_returns_403(client, app):
    _install_identity(app, _identity(tenant_id=""))

    response = client.post(PATH, headers={"Idempotency-Key": "request-1"})

    assert response.status_code == 403


def test_client_identity_fields_are_not_forwarded_to_service(
    client,
    app,
    monkeypatch,
):
    _install_identity(app, _identity(user_id="trusted-user", tenant_id="trusted-tenant"))
    captured = {}

    async def fake_service(**kwargs):
        captured.update(kwargs)
        return _result(reused=False)

    monkeypatch.setattr(cs_dispatch, "create_or_reuse_handoff", fake_service)

    response = client.post(
        PATH,
        headers={"Idempotency-Key": "request-1"},
        json={"user_id": "attacker", "tenant_id": "attacker-tenant"},
    )

    assert response.status_code == 200
    assert captured["user_id"] == "trusted-user"
    assert captured["tenant_id"] == "trusted-tenant"
    assert captured["idempotency_key"] == "request-1"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ConversationForbidden("conversation owner mismatch"), 403),
        (ConversationNotFound("conversation not found"), 404),
    ],
)
def test_service_ownership_errors_are_mapped(
    client,
    app,
    monkeypatch,
    error,
    status_code,
):
    _install_identity(app, _identity())

    async def fake_service(**_kwargs):
        raise error

    monkeypatch.setattr(cs_dispatch, "create_or_reuse_handoff", fake_service)

    response = client.post(PATH, headers={"Idempotency-Key": "request-1"})

    assert response.status_code == status_code


def test_database_failure_returns_503(client, app, monkeypatch):
    _install_identity(app, _identity())

    async def fake_service(**_kwargs):
        raise MemoryDatabaseUnavailable("database is down")

    monkeypatch.setattr(cs_dispatch, "create_or_reuse_handoff", fake_service)

    response = client.post(PATH, headers={"Idempotency-Key": "request-1"})

    assert response.status_code == 503


def test_database_failure_during_session_dependency_returns_503(monkeypatch):
    """连接在 FastAPI 依赖阶段失败时也不能泄漏成 500。"""
    app = FastAPI()
    app.include_router(cs_dispatch.router)
    _install_identity(app, _identity())

    class _UnavailableSession:
        async def __aenter__(self):
            raise MemoryDatabaseUnavailable("database is down")

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        cs_dispatch,
        "AsyncSessionLocal",
        lambda: _UnavailableSession(),
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post(
            PATH,
            headers={"Idempotency-Key": "request-1"},
        )

    assert response.status_code == 503


@pytest.mark.parametrize("reused", [False, True])
def test_success_response_contains_handoff_contract(
    client,
    app,
    monkeypatch,
    reused,
):
    _install_identity(app, _identity())

    async def fake_service(**_kwargs):
        return _result(reused=reused)

    monkeypatch.setattr(cs_dispatch, "create_or_reuse_handoff", fake_service)

    response = client.post(PATH, headers={"Idempotency-Key": "request-1"})

    assert response.status_code == 200
    assert response.json() == {
        "handoff_id": "HANDOFF-1",
        "conversation_id": "conv-1",
        "handoff_state": "waiting_human",
        "total_deadline_at": "2026-09-20T12:00:00+00:00",
        "reused": reused,
    }


def test_main_router_registers_user_handoff_route():
    from backend.app.api.router import api_router

    assert any(
        route.path == "/cs/conversations/{conversation_id}/handoff"
        and "POST" in route.methods
        for route in api_router.routes
    )
