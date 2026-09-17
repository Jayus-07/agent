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


class RatingRequest(BaseModel):
    rating: int
    comment: str | None = None


class CSStatsResponse(BaseModel):
    """管理端统计汇总（满意度 + 意图分布 + 转人工率）"""
    session_count: int = 0
    message_count: int = 0
    rated_count: int = 0
    avg_rating: float | None = None
    rating_dist: dict[int, int] = {}
    intent_dist: list[dict] = []   # [{name, count}]
    handoff_count: int = 0


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


# ── 满意度评分与统计（014_cs_rating） ──────────────────
# 注意：GET /stats 必须注册在 GET /{conversation_id} 之前，否则 "stats" 被当作 conversation_id

@router.get("/stats", response_model=CSStatsResponse)
async def cs_stats():
    """管理端统计汇总：会话/消息量、满意度均分与分布、意图分布、转人工会话数。"""
    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_cs_stats(run_sync)
    except Exception as e:
        logger.warning(f"[CSAdmin] stats failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/rating")
async def rate_conversation(conversation_id: str, body: RatingRequest):
    """用户端满意度评分：1-5 星 + 选填备注，写入 conversations 行。"""
    if not (1 <= body.rating <= 5):
        raise HTTPException(422, detail="rating 必须为 1-5 的整数")

    from datetime import datetime, timezone

    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            conv = (
                await db.execute(
                    select(CSConversation).where(
                        CSConversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if conv is None:
                raise HTTPException(404, detail="Conversation not found")

            conv.rating = body.rating
            conv.rating_comment = (body.comment or "").strip() or None
            conv.rated_at = datetime.now(timezone.utc)
            # commit 后实例过期，作用域外不可再访问（同 close 端点的处理）
            rated_at_iso = conv.rated_at.isoformat()
            await db.commit()

        # 广播给坐席工作台（会话列表角标实时刷新）
        try:
            from backend.customer_service.realtime import get_agent_hub

            get_agent_hub().publish(
                "conversation.rated",
                conversation_id=conversation_id,
                rating=body.rating,
            )
        except Exception:
            pass  # 广播失败不影响评分落库

        return {
            "conversation_id": conversation_id,
            "rating": body.rating,
            "rated_at": rated_at_iso,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] rate_conversation failed: {e}")
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
    """坐席工作台待接入队列（WS 降级轮询源 / 初始全量拉取）。"""
    state_list = (
        [s.strip() for s in states.split(",") if s.strip()] if states else None
    )
    try:
        from backend.customer_service._db_loop import run_sync
        return await _async_handoff_queue(state_list, run_sync)
    except Exception as e:
        logger.warning(f"[CSAdmin] handoff queue failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/agent/ws-ticket")
async def issue_agent_ws_ticket():
    """签发坐席 WS 一次性连接票据（60s TTL、单次使用）。

    鉴权链：本端点受 X-API-Key 保护（BFF 服务端注入），浏览器持 ticket
    完成 WS 握手 —— API Key 不进浏览器。路径注册在 /{conversation_id}/*
    之前，"agent" 不会被当作 conversation_id。
    """
    from backend.customer_service.realtime import (
        TICKET_TTL_SECONDS,
        get_agent_hub,
    )

    return {
        "ticket": get_agent_hub().issue_ticket(),
        "ws_path": "/ws/cs/agent",
        "ttl": TICKET_TTL_SECONDS,
    }


@router.post("/{conversation_id}/close")
async def close_conversation(conversation_id: str, body: ClaimRequest):
    """坐席关闭会话：waiting_human / human_active → closed。

    关闭后同步失效 HandoffStore L1 缓存（否则用户侧下个 turn 仍读到
    缓存里的排队中状态），并向坐席侧广播 conversation.closed。
    """
    if not body.agent_id.strip():
        raise HTTPException(422, detail="agent_id is required")

    from datetime import datetime, timezone

    from backend.customer_service import handoff as handoff_sm
    from backend.customer_service.errors import BusinessRuleError

    try:
        from sqlalchemy import select

        from backend.customer_service.models.handoff import CSHandoff
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(CSHandoff).where(
                        CSHandoff.conversation_id == conversation_id,
                        CSHandoff.handoff_state != "closed",
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    404, detail="No open handoff for conversation"
                )

            current = handoff_sm.HandoffState(row.handoff_state)
            handoff_sm.transition(
                current, handoff_sm.HandoffState.CLOSED,
            )
            row.handoff_state = "closed"
            row.closed_at = datetime.now(timezone.utc)
            # 会话关闭前捕获（commit 后实例过期，作用域外不可再访问）
            handoff_user_id = row.user_id

            # 会话处理模式归位（human / waiting_human → ai）
            from sqlalchemy import update

            from backend.customer_service.models.conversation import (
                CSConversation,
            )

            await db.execute(
                update(CSConversation)
                .where(CSConversation.handling_mode != "ai")
                .where(
                    CSConversation.conversation_id == conversation_id
                )
                .values(
                    handling_mode="ai",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

        # L1 缓存失效（缓存 key = (user_id, session_id)，handoff 行的
        # conversation_id 即 session_id，user_id 行上有）
        from backend.customer_service.handoff_store import (
            get_handoff_store,
        )

        get_handoff_store().invalidate(handoff_user_id, conversation_id)

        # 广播：工作台队列摘除 + 用户侧卡片状态刷新
        from backend.customer_service.realtime import get_agent_hub

        get_agent_hub().publish(
            "conversation.closed",
            conversation_id=conversation_id,
            closed_by=body.agent_id.strip(),
        )

        return {
            "conversation_id": conversation_id,
            "handoff_state": "closed",
            "closed_by": body.agent_id.strip(),
        }
    except HTTPException:
        raise
    except BusinessRuleError as e:
        raise HTTPException(409, detail=str(e))
    except Exception as e:
        logger.warning(f"[CSAdmin] close failed: {e}")
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


async def _async_cs_stats(run_sync) -> CSStatsResponse:
    from sqlalchemy import distinct, func, select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        session_count = int(
            (await db.execute(select(func.count()).select_from(CSConversation))).scalar() or 0
        )
        message_count = int(
            (await db.execute(select(func.count()).select_from(CSMessage))).scalar() or 0
        )

        # 满意度：均分 + 1-5 分布
        rating_rows = (
            await db.execute(
                select(CSConversation.rating, func.count())
                .where(CSConversation.rating.isnot(None))
                .group_by(CSConversation.rating)
            )
        ).all()
        rating_dist: dict[int, int] = {}
        rated_total = 0
        rated_sum = 0
        for rating_val, cnt in rating_rows:
            rating_dist[int(rating_val)] = int(cnt)
            rated_total += int(cnt)
            rated_sum += int(rating_val) * int(cnt)

        # 意图分布（来自消息级意图识别，取 top 6）
        intent_rows = (
            await db.execute(
                select(CSMessage.intent_name, func.count())
                .where(CSMessage.intent_name.isnot(None))
                .group_by(CSMessage.intent_name)
                .order_by(func.count().desc())
                .limit(6)
            )
        ).all()
        intent_dist = [{"name": name, "count": int(cnt)} for name, cnt in intent_rows]

        # 转人工：出现 handoff 记录的会话数（去重）
        handoff_count = int(
            (
                await db.execute(
                    select(func.count(distinct(CSHandoff.conversation_id)))
                )
            ).scalar() or 0
        )

    return CSStatsResponse(
        session_count=session_count,
        message_count=message_count,
        rated_count=rated_total,
        avg_rating=round(rated_sum / rated_total, 2) if rated_total else None,
        rating_dist=rating_dist,
        intent_dist=intent_dist,
        handoff_count=handoff_count,
    )


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

        # commit 前捕获（expire_on_commit=False 虽安全，仍按 close 同款收口）
        handoff_user_id = row.user_id
        row.handoff_state = handoff_sm.HandoffState.HUMAN_ACTIVE.value
        await db.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(handling_mode="human", updated_at=datetime.now(timezone.utc))
        )
        await db.commit()

    # L1 缓存失效：用户侧下个 turn 的 state loader 才能从 DB 读到
    # human_active（否则仍读缓存里的 waiting_human，回复话术滞后一档）
    from backend.customer_service.handoff_store import get_handoff_store

    get_handoff_store().invalidate(handoff_user_id, conversation_id)

    # 广播：其他坐席队列摘除该会话 / 用户侧卡片切「人工已接入」
    from backend.customer_service.realtime import get_agent_hub

    get_agent_hub().publish(
        "conversation.claimed",
        conversation_id=conversation_id,
        agent_id=agent_id,
    )

    return {
        "conversation_id": conversation_id,
        "handoff_state": "human_active",
        "agent_id": agent_id,
        "already_claimed": False,
    }


async def _async_agent_message(conversation_id: str, agent_id: str, content: str, run_sync):
    from sqlalchemy import select

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

        # 会话行可能尚不存在（用户侧 CS turn 落库是 fire-and-forget），
        # messages.conversation_id 有 FK，先 get_or_create 兜底
        from backend.customer_service.managers.conversation_manager import (
            ConversationManager,
        )
        conv_mgr = ConversationManager(db)
        await conv_mgr.get_or_create(conversation_id, row.user_id)

        from backend.customer_service.managers.message_manager import MessageManager

        mgr = MessageManager(db)
        msg = await mgr.save_human_agent_message(
            conversation_id, content, sender_id=agent_id
        )
        await db.commit()

        # 会话关闭前捕获字段（commit 后实例过期，退出作用域后不可再访问）
        msg_payload = {
            "message_id": msg.message_id,
            "sender_type": msg.sender_type,
            "content": msg.content,
            "content_type": msg.content_type,
            "created_at": (
                msg.created_at.isoformat() if msg.created_at else ""
            ),
        }
        msg_pk = msg.id

    # 广播：坐席消息实时推给用户侧/其他坐席订阅（last_id 作增量游标）
    from backend.customer_service.realtime import get_agent_hub

    get_agent_hub().publish(
        "message.created",
        conversation_id=conversation_id,
        last_id=msg_pk,
        message=msg_payload,
    )

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
            # 2026-09-17: 无进行中工单返回 none（原为 closed，会让没有
            # 转人工记录的新会话在用户侧误显示「人工服务已结束」）
            handoff_state=handoff_row.handoff_state if handoff_row else "none",
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
