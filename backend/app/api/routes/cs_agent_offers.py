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
from datetime import datetime, timezone

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
    """为路由提供独立请求事务。

    P3 事务统一（2026-09-21）：写端点以 ``async with session.begin():``
    持有事务（route 拥有 begin/commit/rollback），身份反查（require_agent /
    _resolve_agent_id）使用各自独立的短会话，避免 autobegin 污染路由事务。
    """
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
    request: Request,
) -> AgentContext:
    """接单/拒单/我的 offer 的身份闸（独立短会话，不占用路由事务）。"""
    try:
        async with AsyncSessionLocal() as session:
            agent_id, tenant_id, user_id, roles = await _resolve_agent_id(
                request, session
            )
            cs_role = await repository.find_agent_role(
                session, tenant_id=tenant_id, agent_id=agent_id
            )
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
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
        # P3 事务统一：route 持有事务，block 退出即提交（响应发出前落库
        # 可见 —— 「接单→立即发消息」不再读到旧状态误报 409）。
        async with session.begin():
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
        # P3 事务统一：route 持有事务（同 accept 端点注释）。
        async with session.begin():
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
    # 身份/角色反查用独立短会话，不占用路由事务（P3）
    try:
        async with AsyncSessionLocal() as ident_session:
            actor_id, tenant_id, _user_id, roles = await _resolve_agent_id(
                request, ident_session, allow_unbound_admin=True
            )
            cs_role = await repository.find_agent_role(
                ident_session, tenant_id=tenant_id, agent_id=actor_id
            )
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    context = AgentContext(
        agent_id=actor_id,
        tenant_id=tenant_id,
        user_id=_user_id,
        cs_role=str(cs_role) if cs_role else None,
        platform_roles=tuple(roles),
    )
    _require_supervisor(context)

    try:
        # P3 事务统一：route 持有事务（同 accept 端点注释）。
        async with session.begin():
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


@router.get("/cs/agents")
async def list_tenant_agents(context: AgentContext = Depends(require_agent)):
    """本租户启用坐席清单（P3）：转接弹窗的目标选择数据源。

    含工作状态与技能组（不在线/小休的坐席由前端置灰）；不含负载细节
    （busy_agent_ids 属 dispatcher 内部口径，前端以 offer/认领结果为准）。
    """
    from sqlalchemy import select

    from backend.customer_service.models.agent import CSAgent

    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        CSAgent.agent_id,
                        CSAgent.display_name,
                        CSAgent.role,
                        CSAgent.skill,
                        CSAgent.status,
                        CSAgent.accepting,
                    )
                    .where(
                        CSAgent.tenant_id == context.tenant_id,
                        CSAgent.enabled.is_(True),
                    )
                    .order_by(CSAgent.agent_id.asc())
                )
            ).all()
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return {
        "agents": [
            {
                "agent_id": r.agent_id,
                "display_name": r.display_name,
                "role": r.role,
                "skill": r.skill,
                "status": r.status,
                "accepting": bool(r.accepting),
                "transferable": (r.status or "online") in ("online", "busy"),
            }
            for r in rows
        ],
    }


# ── cs-workstation-v2 P3：坐席工作状态 + 坐席间转接 ──────────────

_AGENT_STATUSES = ("online", "busy", "away", "offline")


class AgentStatusBody(BaseModel):
    # P8：status 与 accepting 至少一项（accepting 单独更新即「接单开关」，
    # 兼容 P3 前端只传 status 的调用）
    status: str | None = None
    accepting: bool | None = None


class TransferBody(BaseModel):
    target_agent_id: str
    reason: str


@router.get("/cs/agents/me/status")
async def get_my_status(context: AgentContext = Depends(require_agent)):
    """本人工作状态（P3）：DB 持久状态 + Redis presence 在线判定。"""
    try:
        async with AsyncSessionLocal() as session:
            row = await repository.find_agent(
                session, tenant_id=context.tenant_id, agent_id=context.agent_id,
            )
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    if row is None:
        raise HTTPException(403, detail="当前用户未绑定启用的客服坐席")
    from backend.customer_service.dispatch.presence import online_agent_ids

    online = bool(await online_agent_ids(
        tenant_id=context.tenant_id, agent_ids=[context.agent_id],
    ))
    return {
        "agent_id": context.agent_id,
        "display_name": row.display_name,
        "status": row.status,
        "accepting": bool(row.accepting),
        "skill": row.skill,
        "presence_online": online,
        # P5：视角过滤（nav 按 supervisor 隐藏退款审核/看板等入口）
        "cs_role": context.cs_role,
    }


@router.put("/cs/agents/me/status")
async def update_my_status(
    body: AgentStatusBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    """切换工作状态/接单开关（持久）：away/offline 与 accepting=false 均不参与派单。

    事件 agent.status_changed 为瞬态广播（不落库不占 seq）：在线坐席实时
    感知，离线坐席重连后经 GET /status 对账。accepting 单独更新不发事件
    （派单过滤实时查库，P8 接单开关语义：关=不派新单）。
    """
    if body.status is None and body.accepting is None:
        raise HTTPException(422, detail="status 与 accepting 至少提供一项")
    if body.status is not None:
        status = (body.status or "").strip().lower()
        if status not in _AGENT_STATUSES:
            raise HTTPException(422, detail="status 必须为 online/busy/away/offline")
    try:
        async with session.begin():
            row = await repository.find_agent_for_update(
                session, tenant_id=context.tenant_id, agent_id=context.agent_id,
            )
            if row is None:
                raise HTTPException(403, detail="当前用户未绑定启用的客服坐席")
            previous = row.status
            if body.status is not None:
                row.status = status
                from backend.customer_service.realtime import get_agent_hub

                get_agent_hub().publish(
                    "agent.status_changed",
                    persist=False,
                    agent_id=context.agent_id,
                    tenant_id=context.tenant_id,
                    previous_status=str(previous or ""),
                    status=status,
                )
            if body.accepting is not None:
                row.accepting = bool(body.accepting)
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return {
        "agent_id": context.agent_id,
        "status": str(row.status),
        "accepting": bool(row.accepting),
    }


@router.post("/cs/conversations/{conversation_id}/transfer")
async def transfer_conversation(
    conversation_id: str,
    body: TransferBody,
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    """坐席把**自己名下**的会话转接给指定坐席（P3）。

    - 仅会话当前认领坐席可发起（403）；
    - 目标必须为本租户启用坐席且工作状态 online/busy（小休/离线不可转，422）；
    - 复用 reassign_handoff：解除当前分配 → 定向 offer（assignment_version+1、
      重试预算重计、offer 超时语义不变）；目标拒单/offer 超时后工单回到
      waiting_human 队列（原坐席可在队列再次认领）；
    - 事件：conversation.reassigned + 定向 conversation.offered（reassign
      内建）+ conversation.transfer_requested（审计/通知中心）。
    """
    target = (body.target_agent_id or "").strip()
    reason = (body.reason or "").strip()
    if not target:
        raise HTTPException(422, detail="target_agent_id 必填")
    if len(reason) < 1:
        raise HTTPException(422, detail="转接原因必填")
    if target == context.agent_id:
        raise HTTPException(422, detail="不能转接给自己")

    from sqlalchemy import select

    from backend.customer_service.dispatch.outbox import append_event
    from backend.customer_service.models.agent import CSAgent
    from backend.customer_service.models.handoff import CSHandoff

    try:
        async with session.begin():
            target_row = (
                await session.execute(
                    select(CSAgent).where(
                        CSAgent.tenant_id == context.tenant_id,
                        CSAgent.agent_id == target,
                        CSAgent.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target_row is None:
                raise HTTPException(422, detail="目标坐席不存在或未启用")
            if (target_row.status or "online") in ("away", "offline"):
                raise HTTPException(
                    422, detail="目标坐席处于小休/离线状态，暂不可转接",
                )
            handoff = (
                await session.execute(
                    select(CSHandoff).where(
                        CSHandoff.tenant_id == context.tenant_id,
                        CSHandoff.conversation_id == conversation_id,
                    )
                )
            ).scalar_one_or_none()
            if handoff is None:
                raise HTTPException(404, detail="会话无进行中的人工工单")
            if str(handoff.assigned_agent_id or "") != context.agent_id:
                raise HTTPException(403, detail="仅会话当前认领坐席可发起转接")
            if handoff.handoff_state not in ("human_active", "agent_offered"):
                raise HTTPException(
                    409,
                    detail=f"当前状态 {handoff.handoff_state} 不允许转接",
                )
            result = await offers_service.reassign_handoff(
                session,
                tenant_id=context.tenant_id,
                supervisor_agent_id=context.agent_id,
                handoff_id=handoff.handoff_id,
                target_agent_id=target,
                reason=reason,
            )
            append_event(
                session,
                tenant_id=context.tenant_id,
                conversation_id=conversation_id,
                type="conversation.transfer_requested",
                payload={
                    "conversation_id": conversation_id,
                    "from_agent_id": context.agent_id,
                    "to_agent_id": target,
                    "reason": reason[:200],
                },
                handoff_id=handoff.handoff_id,
                target_agent_id=target,
                actor_user_id=context.user_id,
                now=datetime.now(timezone.utc),
            )
    except OfferNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ReassignForbidden as exc:
        raise HTTPException(403, detail=str(exc)) from exc
    except ReassignConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc
    return {
        "conversation_id": conversation_id,
        "handoff_state": result.handoff_state,
        "from_agent_id": context.agent_id,
        "to_agent_id": target,
    }
