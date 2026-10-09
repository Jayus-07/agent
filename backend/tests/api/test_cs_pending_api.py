"""GET /cs/conversations/my/{id}/pending 的身份与安全投影回归。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_admin


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cs_admin.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fake_identity(monkeypatch):
    import backend.app.api.identity as identity_mod

    identity = SimpleNamespace(
        user_id="user-1", tenant_id="tenant-1", authenticated=True,
    )
    monkeypatch.setattr(identity_mod, "resolve_identity", lambda request: identity)
    return identity


@pytest.fixture
def fake_owner_check(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def ensure(request, conversation_id):
        calls.append((request.url.path, conversation_id))

    monkeypatch.setattr(cs_admin, "_ensure_my_conversation", ensure)
    return calls


@pytest.fixture
def fake_store(monkeypatch):
    import backend.customer_service.confirmation_store as store_mod

    holder = SimpleNamespace(calls=[], result=None, error=None)

    def load(user_id, conversation_id, tenant_id):
        holder.calls.append((user_id, conversation_id, tenant_id))
        if holder.error:
            raise holder.error
        return holder.result

    monkeypatch.setattr(
        store_mod, "get_confirmation_store",
        lambda: SimpleNamespace(load_authoritative=load),
    )
    return holder


@pytest.fixture
def fake_handoff(monkeypatch):
    import backend.customer_service.handoff.lifecycle as lifecycle_mod

    holder = SimpleNamespace(result=None)
    monkeypatch.setattr(
        lifecycle_mod, "load_active_handoff_sync",
        lambda tenant_id, conversation_id, **kwargs: holder.result,
    )
    return holder


def test_returns_owner_scoped_safe_snapshot_and_masks_pii(
    client, fake_identity, fake_owner_check, fake_store, fake_handoff,
):
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fake_store.result = {
        "action_id": "proposal-1",
        "proposal_id": "proposal-1",
        "version": 3,
        "action_type": "refund",
        "target_type": "order",
        "target_id": "13800138000",
        "proposal_text": "将退款退到13800138000",
        "expires_at": expires_at,
        "confirmation_state": "pending_confirmation",
        "user_id": "must-not-leak",
        "tenant_id": "tenant-1",
        "audit": {"secret": "must-not-leak"},
    }

    response = client.get("/cs/conversations/my/conversation-1/pending")

    assert response.status_code == 200
    assert response.json()["pending_action"] == {
            "proposal_id": "proposal-1",
            "version": 3,
            "action_type": "refund",
            "masked_target": "订单尾号 ****8000",
            "summary": "将退款退到订单尾号 ****8000",
            "expires_at": expires_at,
            "state": "pending",
    }
    assert "13800138000" not in response.text
    assert "must-not-leak" not in response.text
    assert fake_owner_check == [
        ("/cs/conversations/my/conversation-1/pending", "conversation-1"),
    ]
    assert fake_store.calls == [("user-1", "conversation-1", "tenant-1")]


def test_no_pending_returns_explicit_null(
    client, fake_identity, fake_owner_check, fake_store, fake_handoff,
):
    response = client.get("/cs/conversations/my/conversation-1/pending")

    assert response.status_code == 200
    assert response.json() == {"pending_action": None}


def test_pending_is_marked_paused_while_handoff_is_active(
    client, fake_identity, fake_owner_check, fake_store, fake_handoff,
):
    fake_store.result = {
        "proposal_id": "proposal-1", "version": 1, "action_type": "refund",
        "proposal_text": "申请退款", "confirmation_state": "pending",
    }
    fake_handoff.result = {"handoff_state": "human_active"}

    response = client.get("/cs/conversations/my/conversation-1/pending")

    assert response.status_code == 200
    assert response.json()["pending_action"]["state"] == "paused_handoff"


def test_guest_is_rejected_before_confirmation_store_read(
    client, monkeypatch, fake_owner_check, fake_store,
):
    import backend.app.api.identity as identity_mod

    monkeypatch.setattr(
        identity_mod, "resolve_identity",
        lambda request: SimpleNamespace(user_id="", tenant_id="", authenticated=False),
    )
    async def reject_owner(request, conversation_id):
        raise HTTPException(401, detail="login required")

    monkeypatch.setattr(cs_admin, "_ensure_my_conversation", reject_owner)

    response = client.get("/cs/conversations/my/conversation-1/pending")

    assert response.status_code == 401
    assert fake_store.calls == []


def test_store_failure_fails_closed(
    client, fake_identity, fake_owner_check, fake_store, fake_handoff,
):
    from backend.customer_service.confirmation_store import StoreWriteError

    fake_store.error = StoreWriteError("ConfirmationStore", "authoritative_load")

    response = client.get("/cs/conversations/my/conversation-1/pending")

    assert response.status_code == 503
