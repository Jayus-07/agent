"""客服坐席访问权限回归测试。"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.identity import Identity
from backend.app.api.routes import cs_admin


def _request() -> Request:
    return Request({"type": "http", "headers": []})


def test_admin_operator_can_read_another_user_conversation(monkeypatch):
    """管理端 admin JWT 可以读取客户会话，不应触发客户归属 403。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="seat-1",
            user_name="seat-1",
            auth_type="jwt",
            source="header",
            roles=("admin",),
        ),
    )

    cs_admin._ensure_conversation_access(_request(), "customer-1")


def test_viewer_cannot_read_another_user_conversation(monkeypatch):
    """普通 viewer 仍不能越权读取客户会话。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="viewer-1",
            user_name="viewer-1",
            auth_type="jwt",
            source="header",
            roles=("viewer",),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        cs_admin._ensure_conversation_access(_request(), "customer-1")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "无权访问他人会话"


def test_viewer_cannot_claim_conversation(monkeypatch):
    """普通 viewer 不能把自己伪装成坐席执行认领。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="viewer-1",
            user_name="viewer-1",
            auth_type="jwt",
            source="header",
            roles=("viewer",),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        cs_admin._resolve_agent_identity(_request(), "agent-1")

    assert exc_info.value.status_code == 403
