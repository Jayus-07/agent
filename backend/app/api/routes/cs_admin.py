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
