"""GET /cs/conversations/{id}/pending-proposal 坐席可见性与快照回归（cs-workstation-v2 P0）。

覆盖：supervisor/admin/所属坐席三档放行、未认领坐席/普通用户/跨租户拒绝、
服务间通道放行、无 pending 显式空、handoff 暂停与过期口径、DB 失败 fail-closed。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import cs_admin


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cs_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _identity(roles=("supervisor",), tenant_id="tenant-1", authenticated=True):
    return SimpleNamespace(
        user_id="op-1", tenant_id=tenant_id, authenticated=authenticated,
        roles=tuple(roles),
    )


def _patch_identity(monkeypatch, ident):
    import backend.app.api.identity as identity_mod

    monkeypatch.setattr(identity_mod, "resolve_identity", lambda request: ident)


@pytest.fixture
def fake_conv(monkeypatch):
    row = SimpleNamespace(
        user_id="user-9", tenant_id="tenant-1", assigned_agent_id="agent-77",
    )
    holder = SimpleNamespace(row=row, calls=[])

    async def load(conversation_id):
        holder.calls.append(conversation_id)
        return holder.row

    monkeypatch.setattr(cs_admin, "_load_conversation_access_row", load)
    return holder


@pytest.fixture
def fake_bound_agent(monkeypatch):
    holder = SimpleNamespace(agent_id="agent-77")

    async def lookup(*, tenant_id, user_id):
        return holder.agent_id

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", lookup)
    return holder


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


def _pending(**overrides):
    base = {
        "proposal_id": "proposal-1",
        "version": 2,
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "DEMO-1001",
        "proposal_text": "为订单 DEMO-1001 申请退款，联系电话 13800138000",
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        "after_state": {"reason": "商品质量问题"},
    }
    base.update(overrides)
    return base


def test_supervisor_reads_snapshot_with_raw_target_and_masked_pii(
    client, monkeypatch, fake_conv, fake_store, fake_handoff,
):
    _patch_identity(monkeypatch, _identity(roles=("supervisor",)))
    fake_store.result = _pending()

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    body = response.json()["pending_action"]
    assert body["proposal_id"] == "proposal-1"
    assert body["version"] == 2
    assert body["action_type"] == "refund_request"
    assert body["target_type"] == "order"
    assert body["target_id"] == "DEMO-1001"
    assert body["reason"] == "商品质量问题"
    assert body["state"] == "pending"
    assert "DEMO-1001" in body["summary"]
    assert "13800138000" not in response.text
    assert fake_store.calls == [("user-9", "conversation-1", "tenant-1")]


def test_admin_allowed(client, monkeypatch, fake_conv, fake_store, fake_handoff):
    _patch_identity(monkeypatch, _identity(roles=("admin",)))
    fake_store.result = _pending()

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    assert response.json()["pending_action"]["state"] == "pending"


def test_assigned_agent_allowed(
    client, monkeypatch, fake_conv, fake_bound_agent, fake_store, fake_handoff,
):
    _patch_identity(monkeypatch, _identity(roles=("agent",)))
    fake_store.result = _pending()

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    assert response.json()["pending_action"]["proposal_id"] == "proposal-1"


def test_agent_without_assignment_rejected(
    client, monkeypatch, fake_conv, fake_bound_agent, fake_store,
):
    _patch_identity(monkeypatch, _identity(roles=("agent",)))
    fake_conv.row = SimpleNamespace(
        user_id="user-9", tenant_id="tenant-1", assigned_agent_id=None,
    )

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 403
    assert fake_store.calls == []


def test_agent_assigned_to_other_agent_rejected(
    client, monkeypatch, fake_conv, fake_bound_agent, fake_store,
):
    _patch_identity(monkeypatch, _identity(roles=("agent",)))
    fake_conv.row = SimpleNamespace(
        user_id="user-9", tenant_id="tenant-1", assigned_agent_id="agent-88",
    )

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 403
    assert fake_store.calls == []


def test_cross_tenant_supervisor_rejected(
    client, monkeypatch, fake_conv, fake_store,
):
    _patch_identity(monkeypatch, _identity(roles=("supervisor",), tenant_id="tenant-2"))

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 403
    assert fake_store.calls == []


def test_regular_user_rejected(client, monkeypatch, fake_conv, fake_store):
    _patch_identity(monkeypatch, _identity(roles=("viewer", "editor")))

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 403
    assert fake_store.calls == []


def test_unauthenticated_rejected(client, monkeypatch, fake_conv, fake_store):
    _patch_identity(monkeypatch, _identity(authenticated=False))

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 401
    assert fake_store.calls == []


def test_service_channel_allowed(
    client, fake_conv, fake_store, fake_handoff,
):
    from backend.config.auth import AUTH_TYPE_HEADER

    fake_store.result = _pending()

    response = client.get(
        "/cs/conversations/conversation-1/pending-proposal",
        headers={AUTH_TYPE_HEADER: "api-key"},
    )

    assert response.status_code == 200
    assert response.json()["pending_action"]["proposal_id"] == "proposal-1"


def test_no_pending_returns_explicit_null(
    client, monkeypatch, fake_conv, fake_store, fake_handoff,
):
    _patch_identity(monkeypatch, _identity())

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    assert response.json() == {"pending_action": None}


def test_pending_is_marked_paused_while_handoff_active(
    client, monkeypatch, fake_conv, fake_store, fake_handoff,
):
    _patch_identity(monkeypatch, _identity())
    fake_store.result = _pending()
    fake_handoff.result = {"handoff_state": "human_active"}

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    assert response.json()["pending_action"]["state"] == "paused_handoff"


def test_expired_pending_marked_expired(
    client, monkeypatch, fake_conv, fake_store, fake_handoff,
):
    _patch_identity(monkeypatch, _identity())
    fake_store.result = _pending(
        expires_at=(
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat(),
    )

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 200
    assert response.json()["pending_action"]["state"] == "expired"


def test_conversation_not_found(client, monkeypatch, fake_store):
    _patch_identity(monkeypatch, _identity())
    holder = SimpleNamespace(row=None)
    holder.calls = []

    async def load(conversation_id):
        return holder.row

    monkeypatch.setattr(cs_admin, "_load_conversation_access_row", load)

    response = client.get("/cs/conversations/missing/pending-proposal")

    assert response.status_code == 404


def test_store_failure_fails_closed(
    client, monkeypatch, fake_conv, fake_store, fake_handoff,
):
    from backend.customer_service.confirmation_store import StoreWriteError

    _patch_identity(monkeypatch, _identity())
    fake_store.error = StoreWriteError("ConfirmationStore", "authoritative_load")

    response = client.get("/cs/conversations/conversation-1/pending-proposal")

    assert response.status_code == 503
