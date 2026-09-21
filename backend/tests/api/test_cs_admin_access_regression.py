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


async def test_viewer_cannot_claim_conversation(monkeypatch):
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
        await cs_admin._resolve_agent_identity(_request(), "agent-1")

    assert exc_info.value.status_code == 403


async def test_admin_without_bound_agent_cannot_claim(monkeypatch):
    """P7 去手工 agent ID：admin 角色也不能用 user_name 冒充坐席身份。

    绑定关系的唯一权威是 ``cs_agents.auth_user_id``；未绑定启用坐席的用户
    （即使是平台 admin）一律 403，客户端提交的 agent_id 被忽略。
    """
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="admin-1",
            user_name="admin-1",
            auth_type="jwt",
            source="header",
            roles=("admin",),
            tenant_id="tenant-a",
        ),
    )

    async def no_binding(*, tenant_id, user_id):
        return None

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", no_binding)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._resolve_agent_identity(_request(), "agent-1")

    assert exc_info.value.status_code == 403
    assert "未绑定" in exc_info.value.detail


async def test_bound_agent_identity_overrides_client_agent_id(monkeypatch):
    """请求体里的 agent_id 必须被忽略，改用服务端绑定反查结果。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="42",
            user_name="张三",
            auth_type="jwt",
            source="header",
            roles=("admin",),
            tenant_id="tenant-a",
        ),
    )

    async def bound(*, tenant_id, user_id):
        assert (tenant_id, user_id) == ("tenant-a", "42")
        return "agent-bound-7"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", bound)

    resolved = await cs_admin._resolve_agent_identity(_request(), "agent-1")

    assert resolved == "agent-bound-7"


async def test_service_channel_still_honours_declared_agent_id(monkeypatch):
    """服务间 API-Key 通道沿用声明值（BFF 服务端凭据，非浏览器可见）。"""
    request = Request({"type": "http", "headers": [(b"x-auth-type", b"api-key")]})

    assert await cs_admin._resolve_agent_identity(request, "svc-agent") == "svc-agent"

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._resolve_agent_identity(request, "  ")

    assert exc_info.value.status_code == 422
