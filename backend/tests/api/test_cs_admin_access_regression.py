"""客服坐席访问权限回归测试。"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.identity import Identity
from backend.app.api.routes import cs_admin


def _request() -> Request:
    return Request({"type": "http", "headers": []})


async def test_admin_operator_can_read_another_user_conversation(monkeypatch):
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

    await cs_admin._ensure_conversation_access(_request(), "customer-1")


async def test_viewer_cannot_read_another_user_conversation(monkeypatch):
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
        await cs_admin._ensure_conversation_access(_request(), "customer-1")

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


# ── P0 多租户越权修复：_ensure_conversation_access 增加 tenant 校验 ──


def _jwt_request(roles=("viewer",)) -> Request:
    return Request({"type": "http", "headers": []})


def _patch_identity(monkeypatch, *, user_id, tenant_id, roles=("viewer",)):
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id=user_id,
            user_name=user_id,
            auth_type="jwt",
            source="header",
            roles=roles,
            tenant_id=tenant_id,
        ),
    )


async def test_owner_same_tenant_can_access_own_conversation(monkeypatch):
    """tenant A 的 user A 可以 rating 自己的会话（user + tenant 双匹配）。"""
    _patch_identity(monkeypatch, user_id="user-a", tenant_id="tenant-a")

    await cs_admin._ensure_conversation_access(
        _jwt_request(), "user-a", "tenant-a"
    )


async def test_same_tenant_other_user_cannot_rate_others_conversation(monkeypatch):
    """tenant A 的 user B 不能操作 user A 的会话（user 不匹配 → 403）。"""
    _patch_identity(monkeypatch, user_id="user-b", tenant_id="tenant-a")

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(), "user-a", "tenant-a"
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "无权访问他人会话"


async def test_cross_tenant_same_user_id_cannot_rate(monkeypatch):
    """tenant B 即使构造相同/已知 conversation_id（user_id 相同），也不能操作
    tenant A 的会话（tenant 不匹配 → 403）。"""
    _patch_identity(monkeypatch, user_id="user-a", tenant_id="tenant-b")

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(), "user-a", "tenant-a"
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "无权访问其他租户的会话"


async def test_cross_tenant_access_denied_even_for_same_display_name(monkeypatch):
    """两租户下 user_id 不同但同名场景：tenant B 用户访问 tenant A 会话 403。"""
    _patch_identity(monkeypatch, user_id="张三", tenant_id="tenant-b")

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(), "张三", "tenant-a"
        )

    assert exc_info.value.status_code == 403


async def test_admin_channel_not_broken_by_tenant_check(monkeypatch):
    """admin 特殊通道跨租户读会话不受影响（既有设计允许）。"""
    _patch_identity(
        monkeypatch, user_id="admin-1", tenant_id="tenant-b", roles=("admin",)
    )

    await cs_admin._ensure_conversation_access(
        _jwt_request(), "user-a", "tenant-a"
    )


async def test_service_api_key_channel_not_broken_by_tenant_check():
    """服务间 API-Key（BFF）通道原行为不被破坏：不校验 user/tenant。"""
    request = Request({"type": "http", "headers": [(b"x-auth-type", b"api-key")]})

    await cs_admin._ensure_conversation_access(request, "user-a", "tenant-a")


async def test_guest_anonymous_conversation_still_allowed():
    """guest 访问匿名会话的原行为保持。"""
    await cs_admin._ensure_conversation_access(_jwt_request(), "anonymous", "default")


async def test_guest_cannot_access_logged_in_user_conversation():
    """guest 不能借 tenant 放行访问登录用户会话。"""
    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(_jwt_request(), "user-a", "tenant-a")

    assert exc_info.value.status_code == 403


# ── 缺陷7（2026-09-23）：当前认领/被派单坐席读会话通道 ──
# 授权语义与 claim/close 的 _assert_current_agent 一致：JWT 反查 cs_agents
# 绑定，绑定结果等于 conv.assigned_agent_id 才放行；不做「所有坐席读所有会话」。


async def test_current_assigned_agent_can_read_conversation(monkeypatch):
    """当前认领坐席可读会话（工作台消息加载 / REST 对账 / 轮询降级的授权基础）。"""
    _patch_identity(
        monkeypatch,
        user_id="agent-user-1",
        tenant_id="tenant-a",
        roles=("agent",),
    )

    async def bound(*, tenant_id, user_id):
        assert (tenant_id, user_id) == ("tenant-a", "agent-user-1")
        return "agent-7"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", bound)

    await cs_admin._ensure_conversation_access(
        _jwt_request(),
        "customer-9",
        "tenant-a",
        assigned_agent_id="agent-7",
    )


async def test_other_bound_agent_cannot_read_claimed_conversation(monkeypatch):
    """其他坐席（非当前认领人）读已认领会话 → 403，IDOR 隔离不放宽。"""
    _patch_identity(
        monkeypatch,
        user_id="agent-user-2",
        tenant_id="tenant-a",
        roles=("agent",),
    )

    async def bound(*, tenant_id, user_id):
        return "agent-8"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", bound)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(),
            "customer-9",
            "tenant-a",
            assigned_agent_id="agent-7",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "无权访问他人会话"


async def test_agent_without_bound_row_cannot_read_claimed_conversation(monkeypatch):
    """roles 含 agent 但未绑定启用坐席（反查 None）→ 走 owner 判定 403。"""
    _patch_identity(
        monkeypatch,
        user_id="ghost-agent",
        tenant_id="tenant-a",
        roles=("agent",),
    )

    async def no_binding(*, tenant_id, user_id):
        return None

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", no_binding)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(),
            "customer-9",
            "tenant-a",
            assigned_agent_id="agent-7",
        )

    assert exc_info.value.status_code == 403


async def test_agent_lookup_error_does_not_grant_access(monkeypatch):
    """坐席反查异常按「非当前坐席」降级，继续 owner 判定（fail-safe 不放行）。"""
    _patch_identity(
        monkeypatch,
        user_id="agent-user-1",
        tenant_id="tenant-a",
        roles=("agent",),
    )

    async def boom(*, tenant_id, user_id):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", boom)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(),
            "customer-9",
            "tenant-a",
            assigned_agent_id="agent-7",
        )

    assert exc_info.value.status_code == 403


async def test_current_agent_without_tenant_claim_still_denied(monkeypatch):
    """身份缺 tenant claim 时不走坐席通道（租户隔离兜底）→ owner 判定 403。"""
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_identity",
        lambda request: Identity(
            user_id="agent-user-1",
            user_name="agent-user-1",
            auth_type="jwt",
            source="header",
            roles=("agent",),
            tenant_id=None,
        ),
    )

    async def bound(*, tenant_id, user_id):
        return "agent-7"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", bound)

    with pytest.raises(HTTPException) as exc_info:
        await cs_admin._ensure_conversation_access(
            _jwt_request(),
            "customer-9",
            "tenant-a",
            assigned_agent_id="agent-7",
        )

    assert exc_info.value.status_code == 403
