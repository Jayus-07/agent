"""客服租户绑定回归（#19 残留 / #24 / #25，2026-11-09）。

实测背景（当前 main）：
- #19 的 claim/close 已在 2026-11-08 加过租户谓词；残留是读消息的坐席分支
  只比对 agent_id，不与会话租户交叉校验。
- #24 http 模式审核不把审核人租户传给业务服务，mock 审核只按 request_id 匹配。
- #25 工单/质检端点用 identity.tenant_id or "default"，与空租户 fail-closed 口径不一致。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.api.identity import Identity, require_tenant_id


# ── #25 空租户 fail-closed ────────────────────────────────────


def test_require_tenant_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        require_tenant_id(Identity(user_id="u-1", tenant_id=""))
    assert exc.value.status_code == 403


def test_require_tenant_returns_value():
    assert require_tenant_id(
        Identity(user_id="u-1", tenant_id="t-1")) == "t-1"


# ── #19 坐席读会话的租户交叉校验 ─────────────────────────────


def _allow_agent(monkeypatch):
    from backend.app.api.routes import cs_admin

    from backend.app.api import identity as identity_mod

    monkeypatch.setattr(
        identity_mod, "resolve_identity",
        lambda _request: Identity(
            user_id="u-1", tenant_id="tenant-a", roles=("agent",),
        ),
    )

    async def _agent_id(**_kw):
        return "agent-a"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", _agent_id)


def test_agent_cannot_read_other_tenant_conversation(monkeypatch):
    from backend.app.api.routes import cs_admin

    _allow_agent(monkeypatch)
    from types import SimpleNamespace
    _Req = SimpleNamespace(headers={}, method="GET")
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.run(cs_admin._ensure_conversation_access(
            _Req, conv_user_id="u-other", conv_tenant_id="tenant-b",
            assigned_agent_id="agent-a",
        ))
    assert exc.value.status_code == 403


def test_agent_can_read_same_tenant_conversation(monkeypatch):
    from backend.app.api.routes import cs_admin

    _allow_agent(monkeypatch)

    from types import SimpleNamespace
    _Req = SimpleNamespace(headers={}, method="GET")
    import asyncio
    asyncio.run(cs_admin._ensure_conversation_access(
        _Req, conv_user_id="u-other", conv_tenant_id="tenant-a",
        assigned_agent_id="agent-a",
    ))


# ── #24 退款审核下发租户 ─────────────────────────────────────


def test_refund_review_passes_tenant_to_business(monkeypatch):
    from backend.customer_service.service.refund_service import RefundService
    from backend.infra.http import business_client

    captured: dict = {}

    def _fake_post(path, json_body=None, **_kw):
        captured["path"] = path
        captured["body"] = json_body
        return {"code": 0, "data": {"request": {}}}

    monkeypatch.setattr(business_client, "post_json_sync", _fake_post)

    RefundService().review_refund_request(
        request_id="req-1", decision="approved", opinion="ok",
        reviewer_id="agent-1", tenant_id="tenant-a",
    )
    assert captured["body"]["tenant_id"] == "tenant-a"
    assert captured["body"]["reviewer_id"] == "agent-1"


def test_refund_review_without_tenant_keeps_old_contract(monkeypatch):
    from backend.customer_service.service.refund_service import RefundService
    from backend.infra.http import business_client

    captured: dict = {}
    monkeypatch.setattr(
        business_client, "post_json_sync",
        lambda path, json_body=None, **_kw: captured.update(body=json_body) or
        {"code": 0, "data": {"request": {}}},
    )
    RefundService().review_refund_request(
        request_id="req-2", decision="rejected", opinion="no",
        reviewer_id="agent-2",
    )
    assert "tenant_id" not in captured["body"]