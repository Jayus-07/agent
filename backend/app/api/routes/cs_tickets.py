"""客服工单 API（批次C）。

- ``GET  /cs/tickets``                      我的工单（登录用户）
- ``GET  /cs/tickets/{ticket_id}``          工单详情（仅本人；404 不区分
                                            不存在/无权，防探测）
- ``GET  /cs/tickets/admin/list``           管理侧列表（supervisor 闸）
- ``PATCH /cs/tickets/admin/{ticket_id}``   管理侧状态流转（supervisor 闸）

身份：用户侧 ``require_identity``（JWT/网关身份头），坐席/管理侧复用
cs_ops 的 supervisor 双档闸（平台 admin 或 cs_agents.role=supervisor）。
非法状态流转（BusinessRuleError）→ 409，与现有路由语义一致。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.api.identity import Identity, require_identity
from backend.app.api.routes.cs_ops import require_cs_supervisor
from backend.customer_service.errors import BusinessRuleError
from backend.customer_service.ticket_store import get_ticket_store

router = APIRouter(prefix="/cs/tickets", tags=["智能客服-工单"])


class TicketDTO(BaseModel):
    ticket_id: str
    type: str
    status: str
    source: str
    conversation_id: str
    user_id: str
    handoff_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    priority: str
    title: str
    description: Optional[str] = None
    resolution: Optional[str] = None
    severity: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    resolved_at: Optional[str] = None
    closed_at: Optional[str] = None


class TicketListResponse(BaseModel):
    items: list[TicketDTO]
    total: int


class TicketTransitionRequest(BaseModel):
    status: str = Field(..., description="目标状态: processing/pending_user/resolved/closed")
    resolution: Optional[str] = Field(None, max_length=2000)


def _dto(d: dict) -> TicketDTO:
    return TicketDTO(**d)


# ── 用户侧 ───────────────────────────────────────────────


@router.get("", response_model=TicketListResponse)
async def list_my_tickets(
    limit: int = Query(20, ge=1, le=100),
    identity: Identity = Depends(require_identity),
):
    """我的工单（时间倒序）。"""
    if not identity.user_id:
        raise HTTPException(403, detail="缺少可信用户身份")
    tickets = await get_ticket_store().async_list_for_user(
        identity.user_id,
        tenant_id=identity.tenant_id or "default",
        limit=limit,
    )
    return TicketListResponse(
        items=[_dto(t) for t in tickets], total=len(tickets),
    )


@router.get("/{ticket_id}", response_model=TicketDTO)
async def get_my_ticket(
    ticket_id: str,
    identity: Identity = Depends(require_identity),
):
    """工单详情（仅创建人可见；404 不区分不存在/无权，防探测）。"""
    if not identity.user_id:
        raise HTTPException(403, detail="缺少可信用户身份")
    ticket = await get_ticket_store().async_get(
        ticket_id, tenant_id=identity.tenant_id or "default",
    )
    if ticket is None or ticket["user_id"] != identity.user_id:
        raise HTTPException(404, detail="ticket not found")
    return _dto(ticket)


# ── 管理侧（supervisor 闸）────────────────────────────────


@router.get("/admin/list", response_model=TicketListResponse)
async def admin_list_tickets(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    identity: Identity = Depends(require_cs_supervisor),
):
    """管理侧工单列表（按状态/类型过滤）。"""
    tickets = await get_ticket_store().async_list_admin(
        tenant_id=identity.tenant_id or "default",
        status=status, type=type, limit=limit,
    )
    return TicketListResponse(
        items=[_dto(t) for t in tickets], total=len(tickets),
    )


@router.patch("/admin/{ticket_id}", response_model=TicketDTO)
async def admin_transition_ticket(
    ticket_id: str,
    body: TicketTransitionRequest,
    identity: Identity = Depends(require_cs_supervisor),
):
    """管理侧状态流转（写审计日志，非法流转 409）。"""
    try:
        ticket = await get_ticket_store().async_transition(
            ticket_id, body.status,
            tenant_id=identity.tenant_id or "default",
            actor=identity.user_id or "",
            resolution=body.resolution,
        )
    except BusinessRuleError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(404, detail="ticket not found")
    return _dto(ticket)
