"""CS Admin 会话管理、监控统计与事件回放子路由。"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.routes.cs_admin_subroutes.base import facade, logger
from backend.app.api.routes.cs_admin_subroutes.schemas import *

router = APIRouter()

@router.get("/", response_model=PaginatedConversations)
async def list_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="last_activity_at cursor for keyset pagination"),
    status: str | None = Query(None, description="Filter by conversation_status"),
    handling_mode: str | None = Query(None),
    user_id: str | None = Query(None),
    q: str | None = Query(None, description="Search in summary/messages"),
    tenant_id: str | None = Query(None, description="显式租户过滤（仅服务间通道可跨租户）"),
):
    """Paginated conversation list ordered by last_activity_at DESC.

    STOP CS-A P0-1：本端点此前无任何权限闸（审计实测 viewer 可全库枚举），
    现统一走 operator 闸 + 租户谓词 —— JWT 操作者只能看本租户，服务间
    通道可跨租户（可显式传 tenant_id 收窄）。
    """
    tenant_scope = facade._require_operator_and_tenant(request)
    if tenant_scope is not None:
        tenant_id = tenant_scope
    if facade._use_java_source():
        try:
            from urllib.parse import urlencode
            params = {k: v for k, v in {
                "limit": limit, "cursor": cursor, "status": status,
                "handling_mode": handling_mode, "user_id": user_id, "q": q,
                "tenant_id": tenant_id,
            }.items() if v is not None}
            return await facade._proxy_to_java(f"/cs/conversations?{urlencode(params)}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[CSAdmin] java proxy list_conversations failed: {e}")
            raise HTTPException(502, detail="business-service unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_list_conversations(
            limit=limit, cursor=cursor, status=status,
            handling_mode=handling_mode, user_id=user_id, q=q,
            tenant_id=tenant_id,
            run_sync=run_sync,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] list_conversations failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/stats", response_model=CSStatsResponse)
async def cs_stats(request: Request):
    """管理端统计汇总：会话/消息量、满意度均分与分布、意图分布、转人工会话数。

    STOP CS-A P0-1/P0-2：operator 闸 + 租户谓词（JWT 操作者只统计本租户，
    服务间通道跨租户）—— 此前任何登录用户可读全库统计。
    """
    tenant_scope = facade._require_operator_and_tenant(request)
    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_cs_stats(run_sync, tenant_id=tenant_scope)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] stats failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/{conversation_id}/events")
async def replay_conversation_events(
    request: Request,
    conversation_id: str,
    after_seq: int = Query(0, ge=0, description="只返回 seq > after_seq 的事件（断线补发游标）"),
    limit: int = Query(200, ge=1, le=500),
):
    """实时事件断线补发（P3.2）：按 seq 升序回放，客户端按 event_id 幂等去重。

    WS/SSE 断线重连后先调本端点补齐缺口（seq > after_seq），再继续收实时流。
    归属校验与 messages 端点同口径：登录用户限本人会话，guest 仅限匿名会话。
    """
    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            conv_row = (
                await db.execute(
                    select(
                        CSConversation.user_id,
                        CSConversation.tenant_id,
                        CSConversation.assigned_agent_id,
                    )
                    .where(CSConversation.conversation_id == conversation_id)
                    .limit(1)
                )
            ).first()
        if conv_row is None:
            raise HTTPException(404, detail="Conversation not found")
        await facade._ensure_conversation_access(
            request,
            conv_row.user_id,
            conv_row.tenant_id,
            assigned_agent_id=conv_row.assigned_agent_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] events ownership check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    try:
        from backend.customer_service.repository.event_repo import EventRepository
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            events = await EventRepository(db).replay(
                conversation_id, after_seq, limit,
                tenant_id=conv_row.tenant_id,
            )
        return {
            "conversation_id": conversation_id,
            "after_seq": after_seq,
            "events": events,
        }
    except Exception as e:
        logger.warning(f"[CSAdmin] events replay failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/rating")
async def rate_conversation(conversation_id: str, body: RatingRequest, request: Request):
    """用户端满意度评分：1-5 星 + 选填备注，写入 conversations 行。"""
    if not (1 <= body.rating <= 5):
        raise HTTPException(422, detail="rating 必须为 1-5 的整数")

    from datetime import datetime, timezone

    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        # P3 事务统一（2026-09-21）：route 持有事务（begin → block 退出提交，
        # 异常回滚），不再手写 commit。
        # P4 事件统一：conversation.rated 进 PG Outbox（与业务同事务提交，
        # relay 异步广播），删除 commit 后 fire-and-forget + except: pass。
        async with AsyncSessionLocal() as db, db.begin():
            conv = (
                await db.execute(
                    select(CSConversation).where(
                        CSConversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one_or_none()
            if conv is None:
                raise HTTPException(404, detail="Conversation not found")

            # P1 归属校验：登录用户只能评自己租户下自己的会话（guest 仅限匿名会话）
            await facade._ensure_conversation_access(
                request, conv.user_id, conv.tenant_id
            )

            conv.rating = body.rating
            conv.rating_comment = (body.comment or "").strip() or None
            conv.rated_at = datetime.now(timezone.utc)
            rated_at_iso = conv.rated_at.isoformat()

            # P4 事件统一：conversation.rated 进 PG Outbox，与评分同事务
            # 提交；广播由 relay 异步完成（不再 fire-and-forget + 吞错）。
            from backend.customer_service.dispatch.outbox import append_event

            append_event(
                db,
                tenant_id=conv.tenant_id,
                conversation_id=conversation_id,
                type="conversation.rated",
                payload={
                    "conversation_id": conversation_id,
                    "rating": body.rating,
                },
                now=datetime.now(timezone.utc),
            )

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


@router.get("/{conversation_id}/pending-proposal")
async def get_conversation_pending_proposal(
    conversation_id: str, request: Request,
) -> dict:
    """坐席侧会话级待确认提案只读快照（cs-workstation-v2 P0）。

    与用户侧 /my/{id}/pending 同源：load_authoritative 唯一事实源、人工
    接管暂停态、过期判定同口径；差别只在可见性（supervisor / 所属坐席，
    见 _ensure_pending_proposal_access）。只读端点：不认领、不消费、
    不改状态 —— 执行仍只走用户确认链路。
    """
    try:
        conv = await facade._load_conversation_access_row(conversation_id)
        if conv is None:
            raise HTTPException(404, detail="Conversation not found")
        await facade._ensure_pending_proposal_access(
            request, conv.tenant_id, assigned_agent_id=conv.assigned_agent_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] pending-proposal access check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    from backend.customer_service.confirmation_store import (
        StoreWriteError,
        get_confirmation_store,
    )

    try:
        pending = get_confirmation_store().load_authoritative(
            conv.user_id, conversation_id, conv.tenant_id or "",
        )
    except StoreWriteError as exc:
        raise HTTPException(503, detail="待确认状态暂不可核验，请稍后重试") from exc
    if pending is None:
        return {"pending_action": None}

    from datetime import datetime, timezone

    from backend.shared.pii_mask import mask_pii

    raw_target = str(pending.get("target_id") or "").strip()
    # 坐席保留完整单号用于关联订单上下文，但手机号等个人信息仍走统一脱敏
    summary, _ = mask_pii(str(pending.get("proposal_text") or "待确认的业务操作"))

    expires_at = pending.get("expires_at")
    if hasattr(expires_at, "isoformat"):
        expires_at = expires_at.isoformat()
    expires_at = str(expires_at) if expires_at else None
    expired = False
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            expired = expiry <= datetime.now(timezone.utc)
        except ValueError:
            # 不可解析的过期时间按失效处理（与用户侧同口径）
            expired = True

    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        handoff = load_active_handoff_sync(
            conv.tenant_id or "", conversation_id, raise_on_error=True,
        )
    except Exception as exc:
        raise HTTPException(503, detail="人工服务状态暂不可核验，请稍后重试") from exc

    handoff_state = str((handoff or {}).get("handoff_state") or "")
    if handoff_state in {
        "handoff_requested", "waiting_human", "agent_offered", "human_active",
    }:
        state = "paused_handoff"
    else:
        state = "expired" if expired else "pending"

    return {
        "pending_action": {
            "proposal_id": str(pending.get("proposal_id") or pending.get("action_id") or ""),
            "version": int(pending.get("version") or pending.get("proposal_version") or 1),
            "action_type": str(pending.get("action_type") or "unknown"),
            "target_type": str(pending.get("target_type") or "unknown"),
            "target_id": raw_target,
            "summary": summary,
            "reason": str(((pending.get("after_state") or {}).get("reason") or "")),
            "expires_at": expires_at,
            "state": state,
        },
    }


@router.get("/{conversation_id}/customer-context")
async def get_conversation_customer_context(
    conversation_id: str, request: Request,
) -> dict:
    """坐席侧客户360 数据（cs-workstation-v2 P2）：档案 + 近期订单 + 余额（只读）。

    可见性与 pending-proposal 快照同闸（supervisor/admin 本租户全量、
    agent 仅当前认领会话）；全部字段只读，不含敏感支付信息。
    """
    try:
        conv = await facade._load_conversation_access_row(conversation_id)
        if conv is None:
            raise HTTPException(404, detail="Conversation not found")
        await facade._ensure_pending_proposal_access(
            request, conv.tenant_id, assigned_agent_id=conv.assigned_agent_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] customer-context access check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    from datetime import date, datetime

    from backend.customer_service.errors import AccountNotFoundError, DatabaseError
    from backend.customer_service.service.account_service import get_account_service
    from backend.customer_service.service.demo_mode import resolve_user_id
    from backend.customer_service.service.mall_service import MallError, MallService
    from backend.customer_service.service.order_service import get_order_service

    def _as_text(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

    business_user = resolve_user_id(conv.user_id)
    try:
        account = get_account_service().query_account(business_user)
        customer = {
            "display_name": account.name or str(conv.user_id),
            "level": account.level,
            "register_time": account.register_time,
        }
    except (AccountNotFoundError, DatabaseError):
        customer = {"display_name": str(conv.user_id), "level": None, "register_time": None}

    try:
        orders = get_order_service().list_recent_orders_with_products(
            business_user, limit=8,
        )
    except Exception as e:  # noqa: BLE001 — 订单库不可用时仍返回档案与余额
        logger.warning(f"[CSAdmin] customer-context orders unavailable: {e}")
        orders = []

    balance = None
    try:
        wallet = MallService().get_wallet(business_user, conv.tenant_id or "")
        balance = {
            "balance": float(wallet.get("balance") or 0),
            "currency": str(wallet.get("currency") or "CNY"),
        }
    except (MallError, Exception) as e:  # noqa: BLE001 — 余额非关键路径
        logger.warning(f"[CSAdmin] customer-context balance unavailable: {e}")

    return {
        "customer": customer,
        "orders": [
            {
                "order_no": str(o.get("order_no") or o.get("id") or ""),
                "total_amount": (
                    float(o["total_amount"])
                    if o.get("total_amount") is not None else None
                ),
                "status": str(o.get("status") or "unknown"),
                "payment_status": (
                    str(o["payment_status"]) if o.get("payment_status") else None
                ),
                "created_at": _as_text(o.get("created_at")),
                "product_summary": str(
                    o.get("product_names") or o.get("item_summary") or ""
                ),
            }
            for o in orders
        ],
        "balance": balance,
    }


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, request: Request):
    """Conversation detail with ordered messages.

    STOP CS-A P0-1：复用 ``_ensure_conversation_access``（owner/当前坐席/
    admin/同租户），不新建第二套 ownership 判断 —— 此前本端点无任何归属
    校验，登录用户可读任意他人会话（审计实测）。java 代理分支的归属校验
    属 business-service 的 cutover 契约（CS_ADMIN_SOURCE 当前为 local）。
    """
    if facade._use_java_source():
        try:
            return await facade._proxy_to_java(f"/cs/conversations/{conversation_id}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[CSAdmin] java proxy get_conversation failed: {e}")
            raise HTTPException(502, detail="business-service unavailable")

    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            conv = (
                await db.execute(
                    select(
                        CSConversation.user_id,
                        CSConversation.tenant_id,
                        CSConversation.assigned_agent_id,
                    )
                    .where(CSConversation.conversation_id == conversation_id)
                    .limit(1)
                )
            ).first()
        if conv is None:
            raise HTTPException(404, detail="Conversation not found")
        await facade._ensure_conversation_access(
            request,
            conv.user_id,
            conv.tenant_id,
            assigned_agent_id=conv.assigned_agent_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] get_conversation ownership check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        result = await facade._async_get_conversation(conversation_id, run_sync)
        if result is None:
            raise HTTPException(404, detail="Conversation not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] get_conversation failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.get("/{conversation_id}/traces")
async def get_conversation_traces(conversation_id: str, request: Request):
    """Resolve distinct trace_ids for a conversation and return trace summaries.

    STOP CS-A P0-1：同 detail 端点，归属校验复用 ``_ensure_conversation_access``
    —— 此前可枚举任意会话的 trace 关联。
    """
    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            conv = (
                await db.execute(
                    select(
                        CSConversation.user_id,
                        CSConversation.tenant_id,
                        CSConversation.assigned_agent_id,
                    )
                    .where(CSConversation.conversation_id == conversation_id)
                    .limit(1)
                )
            ).first()
        if conv is None:
            raise HTTPException(404, detail="Conversation not found")
        await facade._ensure_conversation_access(
            request,
            conv.user_id,
            conv.tenant_id,
            assigned_agent_id=conv.assigned_agent_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] get_conversation_traces ownership check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        trace_ids = await facade._async_get_trace_ids(conversation_id, run_sync)
    except HTTPException:
        raise
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


@router.get("/{conversation_id}/messages", response_model=HandoffMessagesResponse)
async def get_conversation_messages(
    request: Request,
    conversation_id: str,
    since_id: int = Query(0, ge=0, description="只返回 id > since_id 的消息"),
    limit: int = Query(100, ge=1, le=200),
):
    """会话消息增量拉取（用户侧/坐席侧轮询源，private 消息不返回）。"""
    # P1 归属校验：登录用户只能拉自己的会话（guest 仅限匿名会话）
    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            conv = (
                await db.execute(
                    select(
                        CSConversation.conversation_id,
                        CSConversation.user_id,
                        CSConversation.tenant_id,
                        CSConversation.assigned_agent_id,
                    )
                    .where(CSConversation.conversation_id == conversation_id)
                    .limit(1)
                )
            ).first()
        if conv is not None:
            # 缺陷7：认领/被派单坐席同样可读（工作台消息加载 / REST 对账 / 轮询降级）
            await facade._ensure_conversation_access(
                request,
                conv.user_id,
                conv.tenant_id,
                assigned_agent_id=conv.assigned_agent_id,
            )
        else:
            # 会话不存在：与 404 语义一致（不泄露存在性）
            raise HTTPException(404, detail="Conversation not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] messages ownership check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")

    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_messages_since(
            conversation_id, since_id, limit, run_sync
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] messages since failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


async def _async_list_conversations(
    *, limit, cursor, status, handling_mode, user_id, q, tenant_id, run_sync,
) -> PaginatedConversations:
    from datetime import datetime

    from sqlalchemy import desc as desc_col
    from sqlalchemy import func, select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async def _query():
        async with AsyncSessionLocal() as db:
            base = select(CSConversation)
            count_q = select(func.count()).select_from(CSConversation)

            # STOP CS-A P0-2：租户谓词显式化 —— JWT 操作者只见本租户；
            # 服务间通道 tenant_id=None 时跨租户（受信任服务视图）。
            if tenant_id:
                base = base.where(CSConversation.tenant_id == tenant_id)
                count_q = count_q.where(CSConversation.tenant_id == tenant_id)
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


async def _async_cs_stats(run_sync, tenant_id: str | None = None) -> CSStatsResponse:
    from sqlalchemy import distinct, func, select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # STOP CS-A P0-2：统计按租户隔离（服务间通道 tenant_id=None 跨租户）
        conv_base = select(CSConversation)
        msg_base = select(CSMessage)
        if tenant_id:
            conv_base = conv_base.where(CSConversation.tenant_id == tenant_id)

        session_count = int(
            (await db.execute(
                select(func.count()).select_from(conv_base.subquery())
            )).scalar() or 0
        )
        if tenant_id:
            msg_base = msg_base.where(
                CSMessage.conversation_id.in_(
                    select(CSConversation.conversation_id).where(
                        CSConversation.tenant_id == tenant_id,
                    )
                )
            )
        message_count = int(
            (await db.execute(
                select(func.count()).select_from(msg_base.subquery())
            )).scalar() or 0
        )

        # 满意度：均分 + 1-5 分布
        rating_q = (
            select(CSConversation.rating, func.count())
            .where(CSConversation.rating.isnot(None))
            .group_by(CSConversation.rating)
        )
        if tenant_id:
            rating_q = rating_q.where(CSConversation.tenant_id == tenant_id)
        rating_rows = (await db.execute(rating_q)).all()
        rating_dist: dict[int, int] = {}
        rated_total = 0
        rated_sum = 0
        for rating_val, cnt in rating_rows:
            rating_dist[int(rating_val)] = int(cnt)
            rated_total += int(cnt)
            rated_sum += int(rating_val) * int(cnt)

        # 意图分布（来自消息级意图识别，取 top 6）
        intent_q = (
            select(CSMessage.intent_name, func.count())
            .where(CSMessage.intent_name.isnot(None))
            .group_by(CSMessage.intent_name)
            .order_by(func.count().desc())
            .limit(6)
        )
        if tenant_id:
            intent_q = intent_q.where(
                CSMessage.conversation_id.in_(
                    select(CSConversation.conversation_id).where(
                        CSConversation.tenant_id == tenant_id,
                    )
                )
            )
        intent_rows = (await db.execute(intent_q)).all()
        intent_dist = [{"name": name, "count": int(cnt)} for name, cnt in intent_rows]

        # 转人工：出现 handoff 记录的会话数（去重）
        handoff_q = select(func.count(distinct(CSHandoff.conversation_id)))
        if tenant_id:
            handoff_q = handoff_q.where(CSHandoff.tenant_id == tenant_id)
        handoff_count = int((await db.execute(handoff_q)).scalar() or 0)

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
    from backend.customer_service.models.handoff import CSHandoff
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

        handoff_id = await db.scalar(
            select(CSHandoff.handoff_id).where(
                CSHandoff.tenant_id == conv.tenant_id,
                CSHandoff.conversation_id == conv.conversation_id,
                CSHandoff.handoff_state.in_(("agent_offered", "human_active")),
            ).order_by(CSHandoff.id.desc()).limit(1)
        )

        return ConversationDetail(
            conversation_id=conv.conversation_id,
            user_id=conv.user_id,
            conversation_status=conv.conversation_status,
            handling_mode=conv.handling_mode,
            priority=conv.priority,
            channel=conv.channel,
            summary=conv.summary,
            context_summary=conv.context_summary,
            handoff_id=handoff_id,
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
                    attachments=m.attachments,
                    created_at=m.created_at.isoformat() if m.created_at else "",
                )
                for m in messages
            ],
        )


async def _async_get_trace_ids(conversation_id: str, run_sync):
    from sqlalchemy import distinct, select

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


async def _async_messages_since(conversation_id: str, since_id: int, limit: int, run_sync):
    from sqlalchemy import func, select

    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        # D11（2026-11-23 定级 P0）：此处此前过滤 closed，关单后 handoff_row
        # 必为 None → 恒返回 "none"，用户端「人工服务已结束」灰横幅/满意度卡
        # 的 closed 分支永远不可达（实时与回放两侧同时失效）。改为取最新一条
        # handoff：closed 是真实历史终态，返回 closed 让前端结束态正确恢复；
        # 从无人工会话仍返回 none（维持 2026-09-17 修复：新会话不误显示已结束）。
        handoff_row = (
            await db.execute(
                select(CSHandoff).where(
                    CSHandoff.conversation_id == conversation_id,
                ).order_by(CSHandoff.id.desc()).limit(1)
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

        # A 案（2026-10-08）：waiting_human 时计算排队位置与总等待截止，
        # 支撑用户侧倒计时（「剩余 N 秒，超时将自动登记工单」）。
        # closed 行不构造 meta：倒计时/排队位只属于进行中的转人工。
        handoff_meta = None
        if handoff_row is not None and handoff_row.handoff_state != "closed":
            queue_position = 0
            if handoff_row.handoff_state == "waiting_human":
                ahead = (
                    await db.execute(
                        select(func.count(CSHandoff.id)).where(
                            CSHandoff.tenant_id == handoff_row.tenant_id,
                            CSHandoff.handoff_state == "waiting_human",
                            CSHandoff.created_at < handoff_row.created_at,
                        )
                    )
                ).scalar()
                queue_position = int(ahead or 0)
            handoff_meta = HandoffMeta(
                handoff_id=handoff_row.handoff_id,
                handoff_state=handoff_row.handoff_state,
                total_deadline_at=(
                    handoff_row.total_deadline_at.isoformat()
                    if handoff_row.total_deadline_at else None
                ),
                queue_position=queue_position,
            )

        return HandoffMessagesResponse(
            conversation_id=conversation_id,
            # 2026-09-17: 无人工记录的新会话返回 none（不误显示「已结束」）。
            # D11（2026-11-23）：曾转人工且已关单 → 返回 closed，前端结束态
            # （灰横幅+满意度卡）在实时与回放两侧均据此恢复。
            handoff_state=handoff_row.handoff_state if handoff_row else "none",
            last_id=last_id,
            handoff_meta=handoff_meta,
            messages=[
                MessageDTO(
                    message_id=m.message_id,
                    sender_type=m.sender_type,
                    content=m.content,
                    content_type=m.content_type,
                    attachments=m.attachments,
                    created_at=m.created_at.isoformat() if m.created_at else "",
                )
                for m in rows
            ],
        )

# 注册默认实现到 facade
for _fn in [
    _async_list_conversations,
    _async_cs_stats,
    _async_get_conversation,
    _async_get_trace_ids,
    _async_messages_since
]:
    facade.register(_fn.__name__, _fn)
