"""CS Admin API — conversation list / detail / trace linkage.

Prefix: ``/cs/conversations``

Cutover 开关（CS_ADMIN_SOURCE，见 backend/config/messaging.py）:
  - local（默认）：读本地 PostgreSQL（现状，Java 业务系统未验证前的过渡态）
  - java：代理到 business-service（Java 业务系统），本地查询路径停用
traces 端点始终走 Python（trace 数据在 observability.trace_store）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.shared.logger import logger

router = APIRouter(prefix="/cs/conversations", tags=["智能客服-管理"])


def _use_java_source() -> bool:
    from backend.config.messaging import CS_ADMIN_SOURCE
    return CS_ADMIN_SOURCE == "java"


async def _proxy_to_java(path: str) -> dict:
    """代理请求到 business-service（cutover 后的读源）"""
    from backend.infra.http.business_client import BusinessServiceError, get_json

    try:
        return await get_json(path)
    except BusinessServiceError as e:
        if e.status_code == 404:
            raise HTTPException(404, detail="Conversation not found")
        raise HTTPException(502, detail=f"business-service error: {e}")


# ── Response models ──────────────────────────────────────

class MessageDTO(BaseModel):
    message_id: str
    sender_type: str
    content: str
    content_type: str = "text"
    intent_domain: str | None = None
    intent_name: str | None = None
    confidence: float | None = None
    trace_id: str | None = None
    created_at: str


class ConversationSummary(BaseModel):
    conversation_id: str
    user_id: str
    conversation_status: str
    handling_mode: str
    priority: str
    message_count: int = 0
    trace_count: int = 0
    last_trace_id: str | None = None
    last_activity_at: str | None = None
    created_at: str
    summary: str | None = None


class ConversationDetail(BaseModel):
    conversation_id: str
    user_id: str
    conversation_status: str
    handling_mode: str
    priority: str
    channel: str
    summary: str | None = None
    context_summary: str | None = None
    trace_count: int = 0
    last_trace_id: str | None = None
    created_at: str
    last_activity_at: str | None = None
    messages: list[MessageDTO]


class PaginatedConversations(BaseModel):
    items: list[ConversationSummary]
    total: int
    has_more: bool


# ── Endpoints ────────────────────────────────────────────

@router.get("", response_model=PaginatedConversations)
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="last_activity_at cursor for keyset pagination"),
    status: str | None = Query(None, description="Filter by conversation_status"),
    handling_mode: str | None = Query(None),
    user_id: str | None = Query(None),
    q: str | None = Query(None, description="Search in summary/messages"),
):
    """Paginated conversation list ordered by last_activity_at DESC."""
    if _use_java_source():
        try:
            from urllib.parse import urlencode
            params = {k: v for k, v in {
                "limit": limit, "cursor": cursor, "status": status,
                "handling_mode": handling_mode, "user_id": user_id, "q": q,
            }.items() if v is not None}
            return await _proxy_to_java(f"/cs/conversations?{urlencode(params)}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[CSAdmin] java proxy list_conversations failed: {e}")
            raise HTTPException(502, detail="business-service unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_list_conversations(
            limit=limit, cursor=cursor, status=status,
            handling_mode=handling_mode, user_id=user_id, q=q,
            run_sync=run_sync,
        )
    except Exception as e:
        logger.warning(f"[CSAdmin] list_conversations failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str):
    """Conversation detail with ordered messages."""
    if _use_java_source():
        try:
            return await _proxy_to_java(f"/cs/conversations/{conversation_id}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[CSAdmin] java proxy get_conversation failed: {e}")
            raise HTTPException(502, detail="business-service unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        result = await _async_get_conversation(conversation_id, run_sync)
        if result is None:
            raise HTTPException(404, detail="Conversation not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] get_conversation failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/{conversation_id}/traces")
async def get_conversation_traces(conversation_id: str):
    """Resolve distinct trace_ids for a conversation and return trace summaries."""
    try:
        from backend.customer_service._db_loop import run_sync
        trace_ids = await _async_get_trace_ids(conversation_id, run_sync)
    except Exception as e:
        logger.warning(f"[CSAdmin] get_trace_ids failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    from backend.observability.trace_store import get_trace_store
    store = get_trace_store()
    traces = []
    for tid in trace_ids:
        stored = store.get(tid)
        if stored:
            from backend.app.api.routes._trace_dto import stored_dict_to_dto
            traces.append(stored_dict_to_dto(stored))
    return {"conversation_id": conversation_id, "traces": traces}


# ── 人工介入（坐席侧）─────────────────────────────────────
# v1 坐席工作台走轮询（2s），机制说明见 docs/customer-service/演示沙盒方案-2026-09-17.md §七。
# 企业标准是 WebSocket/SSE 推送坐席队列，此处先用轮询保证 APISIX 兼容与实现简单。


class HandoffQueueItem(BaseModel):
    conversation_id: str
    user_id: str
    handoff_state: str
    trigger_type: str | None = None
    trigger_reason: str | None = None
    updated_at: str
    last_message_preview: str | None = None


class HandoffQueueResponse(BaseModel):
    items: list[HandoffQueueItem]
    total: int


class ClaimRequest(BaseModel):
    agent_id: str


class AgentMessageRequest(BaseModel):
    agent_id: str
    content: str


class AgentMessageResponse(BaseModel):
    message_id: str
    sender_type: str
    content: str
    created_at: str


class HandoffMessagesResponse(BaseModel):
    conversation_id: str
    handoff_state: str
    last_id: int
    messages: list[MessageDTO]


@router.get("/handoff/queue", response_model=HandoffQueueResponse)
async def get_handoff_queue(
    states: str | None = Query(
        None,
        description="逗号分隔 handoff 状态过滤，缺省=全部未关闭",
    ),
):
    """坐席工作台待接入队列（轮询源）。"""
    state_list = (
        [s.strip() for s in states.split(",") if s.strip()] if states else None
    )
    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_handoff_queue(state_list, run_sync)
    except Exception as e:
        logger.warning(f"[CSAdmin] handoff queue failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/claim")
async def claim_conversation(conversation_id: str, body: ClaimRequest):
    """坐席认领会话：handoff → human_active。

    仅允许 waiting_human → human_active；handoff_requested 说明用户刚发起、
    尚未进入排队（由客服运行时流转），返回 409 让坐席稍后再认领。
    """
    if not body.agent_id.strip():
        raise HTTPException(422, detail="agent_id is required")

    from backend.customer_service.errors import BusinessRuleError

    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_claim(
            conversation_id, body.agent_id.strip(), run_sync
        )
    except HTTPException:
        raise
    except BusinessRuleError as e:
        raise HTTPException(409, detail=str(e))
    except Exception as e:
        logger.warning(f"[CSAdmin] claim failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/agent-messages", response_model=AgentMessageResponse)
async def post_agent_message(conversation_id: str, body: AgentMessageRequest):
    """坐席发言：落库为 human_agent 消息（用户侧经消息增量接口/后续 SSE 可见）。"""
    if not body.agent_id.strip() or not body.content.strip():
        raise HTTPException(422, detail="agent_id and content are required")

    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_agent_message(
            conversation_id, body.agent_id.strip(), body.content.strip(), run_sync
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] agent message failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/{conversation_id}/messages", response_model=HandoffMessagesResponse)
async def get_conversation_messages(
    conversation_id: str,
    since_id: int = Query(0, ge=0, description="只返回 id > since_id 的消息"),
    limit: int = Query(100, ge=1, le=200),
):
    """会话消息增量拉取（用户侧/坐席侧轮询源，private 消息不返回）。"""
    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_messages_since(
            conversation_id, since_id, limit, run_sync
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] messages since failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


# ── Async helpers ────────────────────────────────────────

async def _async_list_conversations(
    *, limit, cursor, status, handling_mode, user_id, q, run_sync,
) -> PaginatedConversations:
    from datetime import datetime

    from sqlalchemy import func, select
    from sqlalchemy import desc as desc_col

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async def _query():
        async with AsyncSessionLocal() as db:
            base = select(CSConversation)
            count_q = select(func.count()).select_from(CSConversation)

            if status:
                base = base.where(CSConversation.conversation_status == status)
                count_q = count_q.where(CSConversation.conversation_status == status)
            if handling_mode:
                base = base.where(CSConversation.handling_mode == handling_mode)
                count_q = count_q.where(CSConversation.handling_mode == handling_mode)
            if user_id:
                base = base.where(CSConversation.user_id == user_id)
                count_q = count_q.where(CSConversation.user_id == user_id)
            if cursor:
                try:
                    cursor_dt = datetime.fromisoformat(cursor)
                    base = base.where(CSConversation.last_activity_at < cursor_dt)
                except ValueError:
                    pass

            total_result = await db.execute(count_q)
            total = int(total_result.scalar() or 0)

            order_col = desc_col(CSConversation.last_activity_at)
            rows = (
                await db.execute(base.order_by(order_col).limit(limit + 1))
            ).scalars().all()
            rows = list(rows)

            has_more = len(rows) > limit
            rows = rows[:limit]

            items = []
            for r in rows:
                msg_count = (
                    await db.execute(
                        select(func.count())
                        .select_from(CSMessage)
                        .where(CSMessage.conversation_id == r.conversation_id)
                    )
                ).scalar() or 0
                items.append(ConversationSummary(
                    conversation_id=r.conversation_id,
                    user_id=r.user_id,
                    conversation_status=r.conversation_status,
                    handling_mode=r.handling_mode,
                    priority=r.priority,
                    message_count=int(msg_count),
                    trace_count=r.trace_count or 0,
                    last_trace_id=r.last_trace_id,
                    last_activity_at=r.last_activity_at.isoformat() if r.last_activity_at else None,
                    created_at=r.created_at.isoformat() if r.created_at else "",
                    summary=r.summary,
                ))
            return PaginatedConversations(items=items, total=total, has_more=has_more)

    return await _query()


async def _async_get_conversation(conversation_id: str, run_sync):
    from sqlalchemy import select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        conv_result = await db.execute(
            select(CSConversation).where(
                CSConversation.conversation_id == conversation_id
            )
        )
        conv = conv_result.scalar_one_or_none()
        if conv is None:
            return None

        msg_result = await db.execute(
            select(CSMessage)
            .where(
                CSMessage.conversation_id == conversation_id,
                CSMessage.private.is_(False),
            )
            .order_by(CSMessage.created_at)
        )
        messages = list(msg_result.scalars().all())

        return ConversationDetail(
            conversation_id=conv.conversation_id,
            user_id=conv.user_id,
            conversation_status=conv.conversation_status,
            handling_mode=conv.handling_mode,
            priority=conv.priority,
            channel=conv.channel,
            summary=conv.summary,
            context_summary=conv.context_summary,
            trace_count=conv.trace_count or 0,
            last_trace_id=conv.last_trace_id,
            created_at=conv.created_at.isoformat() if conv.created_at else "",
            last_activity_at=conv.last_activity_at.isoformat() if conv.last_activity_at else None,
            messages=[
                MessageDTO(
                    message_id=m.message_id,
                    sender_type=m.sender_type,
                    content=m.content,
                    content_type=m.content_type,
                    intent_domain=m.intent_domain,
                    intent_name=m.intent_name,
                    confidence=m.confidence,
                    trace_id=m.trace_id,
                    created_at=m.created_at.isoformat() if m.created_at else "",
                )
                for m in messages
            ],
        )


async def _async_get_trace_ids(conversation_id: str, run_sync):
    from sqlalchemy import select, distinct

    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(distinct(CSMessage.trace_id))
            .where(
                CSMessage.conversation_id == conversation_id,
                CSMessage.trace_id.isnot(None),
            )
            .order_by(CSMessage.trace_id)
        )
        return [row[0] for row in result.all()]


async def _async_handoff_queue(state_list, run_sync):
    from sqlalchemy import select

    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        from backend.customer_service.repository import HandoffRepository

        repo = HandoffRepository(db)
        rows = await repo.list_open(states=state_list)

        items = []
        for h in rows:
            last_msg = (
                await db.execute(
                    select(CSMessage)
                    .where(
                        CSMessage.conversation_id == h.conversation_id,
                        CSMessage.private.is_(False),
                    )
                    .order_by(CSMessage.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            items.append(HandoffQueueItem(
                conversation_id=h.conversation_id,
                user_id=h.user_id,
                handoff_state=h.handoff_state,
                trigger_type=h.trigger_type,
                trigger_reason=h.trigger_reason,
                updated_at=h.updated_at.isoformat() if h.updated_at else "",
                last_message_preview=(
                    last_msg.content[:80] if last_msg else None
                ),
            ))
        return HandoffQueueResponse(items=items, total=len(items))


async def _async_claim(conversation_id: str, agent_id: str, run_sync):
    """认领会话：waiting_human → human_active + conversation.handling_mode=human。"""
    from datetime import datetime, timezone

    from sqlalchemy import select, update

    from backend.customer_service import handoff as handoff_sm
    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CSHandoff).where(
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail="No open handoff for conversation")
        if row.handoff_state == handoff_sm.HandoffState.HUMAN_ACTIVE.value:
            return {
                "conversation_id": conversation_id,
                "handoff_state": row.handoff_state,
                "agent_id": agent_id,
                "already_claimed": True,
            }

        # 状态机校验（handoff_requested → human_active 为非法转换）
        handoff_sm.transition(
            handoff_sm.HandoffState(row.handoff_state),
            handoff_sm.HandoffState.HUMAN_ACTIVE,
        )

        row.handoff_state = handoff_sm.HandoffState.HUMAN_ACTIVE.value
        await db.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(handling_mode="human", updated_at=datetime.now(timezone.utc))
        )
        await db.commit()

    return {
        "conversation_id": conversation_id,
        "handoff_state": "human_active",
        "agent_id": agent_id,
        "already_claimed": False,
    }


async def _async_agent_message(conversation_id: str, agent_id: str, content: str, run_sync):
    from backend.customer_service.models.handoff import CSHandoff
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CSHandoff).where(
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(404, detail="No open handoff for conversation")
        if row.handoff_state != "human_active":
            raise HTTPException(
                409,
                detail=f"Conversation handoff is {row.handoff_state}, claim it first",
            )

        from backend.customer_service.managers.message_manager import MessageManager

        mgr = MessageManager(db)
        msg = await mgr.save_human_agent_message(
            conversation_id, content, sender_id=agent_id
        )
        await db.commit()

    return AgentMessageResponse(
        message_id=msg.message_id,
        sender_type=msg.sender_type,
        content=msg.content,
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )


async def _async_messages_since(conversation_id: str, since_id: int, limit: int, run_sync):
    from sqlalchemy import select

    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        handoff_row = (
            await db.execute(
                select(CSHandoff).where(
                    CSHandoff.conversation_id == conversation_id,
                    CSHandoff.handoff_state != "closed",
                ).limit(1)
            )
        ).scalar_one_or_none()

        rows = (
            await db.execute(
                select(CSMessage)
                .where(
                    CSMessage.conversation_id == conversation_id,
                    CSMessage.private.is_(False),
                    CSMessage.id > since_id,
                )
                .order_by(CSMessage.id)
                .limit(limit)
            )
        ).scalars().all()

        last_id = max((r.id for r in rows), default=since_id)
        return HandoffMessagesResponse(
            conversation_id=conversation_id,
            handoff_state=handoff_row.handoff_state if handoff_row else "closed",
            last_id=last_id,
            messages=[
                MessageDTO(
                    message_id=m.message_id,
                    sender_type=m.sender_type,
                    content=m.content,
                    content_type=m.content_type,
                    created_at=m.created_at.isoformat() if m.created_at else "",
                )
                for m in rows
            ],
        )
