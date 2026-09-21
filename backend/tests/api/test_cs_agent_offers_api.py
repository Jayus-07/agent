"""P7 坐席 offer API 错误映射测试（方案 §六 P7 完成标准的 403/409/404）。

不走 TestClient（避免拉起全量 app 依赖），直接调用路由函数：
- 身份闸用 monkeypatch 的 ``resolve_identity`` + repository 反查；
- 服务层异常用 monkeypatch 的 ``offers`` 函数抛出；
- 断言路由把它们映射到正确的 HTTPException 状态码。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.api.routes import cs_agent_offers as routes
from backend.customer_service.dispatch import offers as offers_service
from backend.customer_service.dispatch import repository


class _FakeRequest:
    def __init__(self, *, auth_type: str = "", headers: dict | None = None) -> None:
        self.headers = dict(headers or {})
        if auth_type:
            self.headers["X-Auth-Type"] = auth_type


def _identity(roles=("agent",), user_id="user-1", tenant_id="tenant-a"):
    from backend.app.api.identity import Identity

    return Identity(
        user_id=user_id,
        user_name="agent-user",
        auth_type="jwt",
        source="header",
        roles=tuple(roles),
        tenant_id=tenant_id,
    )


@pytest.fixture
def bound_agent(monkeypatch: pytest.MonkeyPatch):
    """当前用户已绑定启用坐席 agent-1（cs_role=agent）。"""

    async def find_agent(*_a, **_k):
        return "agent-1"

    async def find_role(*_a, **_k):
        return "agent"

    monkeypatch.setattr(repository, "find_enabled_agent_id", find_agent)
    monkeypatch.setattr(repository, "find_agent_role", find_role)
    return monkeypatch


def _agent_context() -> routes.AgentContext:
    return routes.AgentContext(
        agent_id="agent-1",
        tenant_id="tenant-a",
        user_id="user-1",
        cs_role="agent",
        platform_roles=("agent",),
    )


class _Session:
    pass


# ── 身份闸 ──────────────────────────────────────────────────


async def test_api_key_channel_is_rejected(bound_agent) -> None:
    request = _FakeRequest(auth_type="api-key")

    with pytest.raises(HTTPException) as exc_info:
        await routes._resolve_agent_id(request, _Session())

    assert exc_info.value.status_code == 403


async def test_unbound_user_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes, "resolve_identity", lambda request: _identity()
    )

    async def no_agent(*_a, **_k):
        return None

    monkeypatch.setattr(repository, "find_enabled_agent_id", no_agent)

    with pytest.raises(HTTPException) as exc_info:
        await routes._resolve_agent_id(_FakeRequest(), _Session())

    assert exc_info.value.status_code == 403
    assert "未绑定" in exc_info.value.detail


# ── accept/decline 错误映射 ─────────────────────────────────


async def test_accept_maps_service_errors(bound_agent, monkeypatch) -> None:
    async def forbidden(*_a, **_k):
        raise offers_service.OfferForbidden("not yours")

    async def stale(*_a, **_k):
        raise offers_service.OfferStale("stale")

    async def missing(*_a, **_k):
        raise offers_service.OfferNotFound("gone")

    cases = [(forbidden, 403), (stale, 409), (missing, 404)]
    for impl, expected in cases:
        monkeypatch.setattr(offers_service, "accept_offer", impl)
        with pytest.raises(HTTPException) as exc_info:
            await routes.accept_offer(
                "hd-1",
                routes.OfferActionBody(offer_version=1),
                context=_agent_context(),
                session=_Session(),
            )
        assert exc_info.value.status_code == expected


async def test_decline_maps_service_errors(bound_agent, monkeypatch) -> None:
    async def forbidden(*_a, **_k):
        raise offers_service.OfferForbidden("not yours")

    async def stale(*_a, **_k):
        raise offers_service.OfferStale("stale")

    monkeypatch.setattr(offers_service, "decline_offer", forbidden)
    with pytest.raises(HTTPException) as exc_info:
        await routes.decline_offer(
            "hd-1", routes.OfferActionBody(), context=_agent_context(), session=_Session()
        )
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(offers_service, "decline_offer", stale)
    with pytest.raises(HTTPException) as exc_info:
        await routes.decline_offer(
            "hd-1", routes.OfferActionBody(), context=_agent_context(), session=_Session()
        )
    assert exc_info.value.status_code == 409


# ── 重派权限 ────────────────────────────────────────────────


async def test_reassign_requires_supervisor_role(
    bound_agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通 agent 角色（且非平台 admin）重派 → 403。"""
    monkeypatch.setattr(
        routes, "resolve_identity", lambda request: _identity(roles=("agent",))
    )
    request = _FakeRequest()

    with pytest.raises(HTTPException) as exc_info:
        await routes.reassign_handoff(
            "hd-1", routes.ReassignBody(), request=request, session=_Session()
        )

    assert exc_info.value.status_code == 403


async def test_reassign_by_platform_admin_without_agent_profile(
    bound_agent, monkeypatch
) -> None:
    """平台 admin 没有坐席档案也可重派（审计留痕身份）。"""
    from backend.customer_service.dispatch import repository as repo

    async def no_agent(*_a, **_k):
        return None

    async def admin_role(*_a, **_k):
        return None

    monkeypatch.setattr(repo, "find_enabled_agent_id", no_agent)
    monkeypatch.setattr(repo, "find_agent_role", admin_role)
    monkeypatch.setattr(
        routes,
        "resolve_identity",
        lambda request: _identity(roles=("admin",)),
    )

    request = _FakeRequest()

    # 身份能过闸（不 403）；后续服务层行为不在本用例范围。
    agent_id, tenant_id, _user, roles = await routes._resolve_agent_id(
        request, _Session(), allow_unbound_admin=True
    )
    assert roles == ("admin",)
    assert tenant_id == "tenant-a"
