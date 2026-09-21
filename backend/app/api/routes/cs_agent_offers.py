"""客服坐席 offer API（P7）。

- ``GET  /cs/agents/me/offers``                      本人待接单 + 处理中工单
- ``POST /cs/agents/me/offers/{handoff_id}/accept``  接单
- ``POST /cs/agents/me/offers/{handoff_id}/decline`` 拒单
- ``POST /cs/handoffs/{handoff_id}/reassign``        主管重派

身份契约（P2/P5 延伸）：坐席 **agent_id 由服务端从
``cs_agents.auth_user_id`` 反查**，浏览器不再提交可信 agent_id。
API-Key 通道没有「按坐席/租户绑定的可信映射」，因此与 WS ticket 一致，
在坐席端点上一律拒绝。

错误语义（方案 §六 P7 完成标准）：

===========  ==========================================
404          handoff 不存在 / 跨租户（不区分，避免探测）
403          工单未分配给当前坐席（accept/decline）
            当前用户未绑定启用坐席 / 无重派权限
409          旧版本 offer、offer 已超时/已回收、状态不允许重派
422          请求体版本号非法
===========  ==========================================
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.identity import resolve_identity
from backend.config.auth import AUTH_TYPE_HEADER
from backend.customer_service.dispatch import offers as offers_service
from backend.customer_service.dispatch import repository
from backend.customer_service.dispatch.offers import (
    OfferForbidden,
    OfferNotFound,
    OfferStale,
    ReassignConflict,
    ReassignForbidden,
)
from backend.memory.database import AsyncSessionLocal, MemoryDatabaseUnavailable

router = APIRouter(tags=["智能客服-坐席 offer"])

_SUPERVISOR_ROLE = "supervisor"


async def get_session():
    """为路由提供独立请求事务。"""
    try:
        async with AsyncSessionLocal() as session:
            yield session
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc


@dataclass(frozen=True)
class AgentContext:
    """已认证坐席上下文（agent_id 来自服务端绑定，不来自请求体）。"""

    agent_id: str
    tenant_id: str
    user_id: str
    cs_role: str | None
    platform_roles: tuple[str, ...]

    @property
    def is_supervisor(self) -> bool:
        return self.cs_role == _SUPERVISOR_ROLE or "admin" in self.platform_roles


class OfferItemDTO(BaseModel):
    handoff_id: str
    conversation_id: str
    user_id: str
    handoff_state: str
    assignment_version: int
    attempt_count: int
    priority: int
    offered_at: str | None
    offer_expires_at: str | None


class OfferListResponse(BaseModel):
    items: list[OfferItemDTO]
    total: int


class OfferActionBody(BaseModel):
    offer_version: int | None = None
    reason: str | None = None


class OfferActionResponse(BaseModel):
    handoff_id: str
    conversation_id: str
    handoff_state: str
    agent_id: str | None
    assignment_version: int
    offer_expires_at: str | None


class ReassignBody(BaseModel):
    target_agent_id: str | None = None
    reason: str | None = None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _is_service_channel(request: Request) -> bool:
    return (request.headers.get(AUTH_TYPE_HEADER) or "").strip().lower() == "api-key"


async def _resolve_agent_id(
    request: Request, session: AsyncSession, *,
    allow_unbound_admin: bool = False,
) -> tuple[str, str, str, tuple[str, ...]]:
    """从可信身份反查启用坐席；返回 (agent_id, tenant_id, user_id, roles)。

    ``allow_unbound_admin``：仅重派端点允许平台 admin 在没有坐席档案时继续
    （其身份只用于审计留痕，不参与任何「谁被分配」的判定）。
    """
    if _is_service_channel(request):
        raise HTTPException(
            403,
            detail="坐席 offer 端点需要绑定客服用户的 JWT 身份，API-Key 通道不支持",
        )

    identity = resolve_identity(request)
    if not identity.authenticated:
        raise HTTPException(401, detail="未认证：缺少坐席身份")
    if not identity.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")

    try:
        agent_id = await repository.find_enabled_agent_id(
            session, tenant_id=identity.tenant_id, auth_user_id=identity.user_id
        )
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc

    if not agent_id:
        if allow_unbound_admin and "admin" in identity.roles:
            return (
                identity.user_name or identity.user_id,
                identity.tenant_id,
                identity.user_id,
                identity.roles,
            )
        raise HTTPException(403, detail="当前用户未绑定启用的客服坐席")
    return str(agent_id), identity.tenant_id, identity.user_id, identity.roles


async def require_agent(
    request: Request, session: AsyncSession = Depends(get_session)
) -> AgentContext:
    """接单/拒单/我的 offer 的身份闸。"""
    agent_id, tenant_id, user_id, roles = await _resolve_agent_id(request, session)
    cs_role = await repository.find_agent_role(
        session, tenant_id=tenant_id, agent_id=agent_id
    )
    return AgentContext(
        agent_id=agent_id,
        tenant_id=tenant_id,
        user_id=user_id,
        cs_role=str(cs_role) if cs_role else None,
        platform_roles=tuple(roles),
    )


def _require_supervisor(context: AgentContext) -> None:
    if not context.is_supervisor:
        raise HTTPException(403, detail="重派需要客服主管或平台管理员权限")


@router.get("/cs/agents/me/offers", response_model=OfferListResponse)
async def list_my_offers(
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> OfferListResponse:
    """本人待接单 + 处理中工单；WS 重连后按此接口补拉，避免漏 offer。"""
    rows = await offers_service.list_my_offers(
        session, tenant_id=context.tenant_id, agent_id=context.agent_id
    )
    items = [
        OfferItemDTO(
            handoff_id=row.handoff_id,
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            handoff_state=row.handoff_state,
            assignment_version=row.assignment_version,
            attempt_count=row.attempt_count,
            priority=row.priority,
            offered_at=_iso(row.offered_at),
            offer_expires_at=_iso(row.offer_expires_at),
        )
        for row in rows
    ]
    return OfferListResponse(items=items, total=len(items))


@router.post(
    "/cs/agents/me/offers/{handoff_id}/accept",
    response_model=OfferActionResponse,
)
async def accept_offer(
    handoff_id: str,
    body: OfferActionBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> OfferActionResponse:
    """接单：``agent_offered → human_active``；旧版本返回 409。"""
    try:
        result = await offers_service.accept_offer(
            session,
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            handoff_id=handoff_id,
            offer_version=body.offer_version,
        )
    except OfferNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except OfferForbidden as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except OfferStale as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return _action_response(result)


@router.post(
    "/cs/agents/me/offers/{handoff_id}/decline",
    response_model=OfferActionResponse,
)
async def decline_offer(
    handoff_id: str,
    body: OfferActionBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> OfferActionResponse:
    """拒单：``agent_offered → waiting_human``；该坐席进入本工单冷却期。"""
    try:
        result = await offers_service.decline_offer(
            session,
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            handoff_id=handoff_id,
            offer_version=body.offer_version,
            reason=body.reason,
        )
    except OfferNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except OfferForbidden as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except OfferStale as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return _action_response(result)


@router.post(
    "/cs/handoffs/{handoff_id}/reassign",
    response_model=OfferActionResponse,
)
async def reassign_handoff(
    handoff_id: str,
    body: ReassignBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> OfferActionResponse:
    """主管重派：解除当前分配，可指定新坐席；``agent`` 角色调用 403。"""
    actor_id, tenant_id, _user_id, roles = await _resolve_agent_id(
        request, session, allow_unbound_admin=True
    )
    context = AgentContext(
        agent_id=actor_id,
        tenant_id=tenant_id,
        user_id=_user_id,
        cs_role=await repository.find_agent_role(
            session, tenant_id=tenant_id, agent_id=actor_id
        ),
        platform_roles=tuple(roles),
    )
    _require_supervisor(context)

    try:
        result = await offers_service.reassign_handoff(
            session,
            tenant_id=context.tenant_id,
            supervisor_agent_id=context.agent_id,
            handoff_id=handoff_id,
            target_agent_id=body.target_agent_id,
            reason=body.reason,
        )
    except OfferNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ReassignForbidden as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except ReassignConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return _action_response(result)


def _action_response(result: offers_service.OfferActionResult) -> OfferActionResponse:
    return OfferActionResponse(
        handoff_id=result.handoff_id,
        conversation_id=result.conversation_id,
        handoff_state=result.handoff_state,
        agent_id=result.agent_id,
        assignment_version=result.assignment_version,
        offer_expires_at=_iso(result.offer_expires_at),
    )
