"""CS Admin 坐席工作台、转接队列与认领流转子路由。"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.routes.cs_admin_subroutes.base import facade, logger
from backend.app.api.routes.cs_admin_subroutes.schemas import *

router = APIRouter()

@router.get("/handoff/queue", response_model=HandoffQueueResponse)
async def get_handoff_queue(
    request: Request,
    states: str | None = Query(
        None,
        description="逗号分隔 handoff 状态过滤，缺省=可认领"
                    "（waiting_human/agent_offered）",
    ),
    mine: bool = Query(
        False,
        description="只看本人处理中（human_active）会话；与 states 互斥，"
                    "坐席身份由服务端按 cs_agents.auth_user_id 反查",
    ),
):
    """坐席工作台待接入队列（WS 降级轮询源 / 初始全量拉取）。

    ``mine=true``（2026-11-22 刷新丢失修复）：工作台「我的会话」tab 的
    服务端数据源——只返回分配给当前坐席的 human_active 会话，使乐观
    更新（认领/接单）与刷新后重拉收敛到同一来源。
    """
    tenant_scope = facade._require_operator_and_tenant(request)
    if mine:
        if facade._is_service_channel(request):
            raise HTTPException(
                422, detail="mine 队列仅支持坐席 JWT 身份（服务间通道无坐席归属）",
            )
        from backend.app.api.identity import resolve_identity

        ident = resolve_identity(request)
        if not ident.authenticated or not ident.tenant_id:
            raise HTTPException(403, detail="缺少可信坐席身份")
        assigned_agent_id = await facade._lookup_bound_agent_id(
            tenant_id=ident.tenant_id, user_id=ident.user_id
        )
        if not assigned_agent_id:
            # 未绑定坐席的 operator 没有「我的会话」：空队列而非报错，
            # 避免工作台轮询每 5s 刷一次 403 噪声。
            return HandoffQueueResponse(items=[], total=0)
        state_list = ["human_active"]
    else:
        assigned_agent_id = None
        state_list = (
            [s.strip() for s in states.split(",") if s.strip()] if states else None
        )
    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_handoff_queue(
            state_list, run_sync, tenant_id=tenant_scope,
            assigned_agent_id=assigned_agent_id,
        )
    except Exception as e:
        logger.warning(f"[CSAdmin] handoff queue failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/agent/ws-ticket")
async def issue_agent_ws_ticket(request: Request):
    """签发坐席 WS 一次性连接票据（60s TTL、单次使用）。

    鉴权链：仅接受 JWT 身份头提供的 user + tenant，坐席 agent_id 必须
    从 PostgreSQL 的 cs_agents.auth_user_id 反查；浏览器不能提交可信
    agent_id。API-Key 通道没有按坐席/租户绑定的可信映射，明确拒绝签发。
    浏览器持 ticket 完成 WS 握手，Redis 不可用时拒绝签发。
    """
    agent_id, tenant_id = await facade._resolve_ws_agent_identity(request)
    from backend.customer_service.realtime import (
        TICKET_TTL_SECONDS,
        get_agent_hub,
    )

    ticket = await asyncio.to_thread(
        get_agent_hub().issue_ticket,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )
    if not ticket:
        raise HTTPException(503, detail="Realtime ticket service unavailable")
    return {
        "ticket": ticket,
        "ws_path": "/ws/cs/agent",
        "ttl": TICKET_TTL_SECONDS,
    }


@router.post("/{conversation_id}/close")
async def close_conversation(conversation_id: str, body: ClaimRequest, request: Request):
    """坐席关闭会话：waiting_human / human_active → closed。

    关闭后同步失效 HandoffStore L1 缓存（否则用户侧下个 turn 仍读到
    缓存里的排队中状态），并向坐席侧广播 conversation.closed。
    P1：agent_id 改为服务端解析；P7（去手工 agent ID）：**已分配出去的
    工单（agent_offered / human_active）只能由被分配坐席关闭**，其他坐席
    一律 409 —— 主管要改派走 /handoffs/{id}/reassign，不是替人关闭。
    """
    agent_id, tenant_id = await facade._resolve_agent_identity_and_tenant(
        request, body.agent_id
    )

    from datetime import datetime, timezone

    from backend.customer_service import handoff as handoff_sm
    from backend.customer_service.errors import BusinessRuleError

    try:
        from sqlalchemy import select

        from backend.customer_service.models.assignment import CSAssignment
        from backend.customer_service.models.conversation import CSConversation
        from backend.customer_service.models.handoff import CSHandoff
        from backend.memory.database import AsyncSessionLocal

        # 2026-11-08 跨租户审计（cs-claim-close.missing-tenant-predicate）：
        # JWT 坐席只能关闭自己租户的会话；API-Key 服务通道保持跨租户视图。
        handoff_where = [
            CSHandoff.conversation_id == conversation_id,
            CSHandoff.handoff_state != "closed",
        ]
        conv_where = [CSConversation.conversation_id == conversation_id]
        if tenant_id is not None:
            handoff_where.append(CSHandoff.tenant_id == tenant_id)
            conv_where.append(CSConversation.tenant_id == tenant_id)

        # P3 事务统一：route 持有事务（同 rating 端点注释）。
        async with AsyncSessionLocal() as db, db.begin():
            row = (
                await db.execute(
                    select(CSHandoff).where(*handoff_where).limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    404, detail="No open handoff for conversation"
                )

            conv = (
                await db.execute(
                    select(CSConversation).where(*conv_where)
                )
            ).scalar_one_or_none()

            current = handoff_sm.HandoffState(row.handoff_state)
            if current in (
                handoff_sm.HandoffState.HUMAN_ACTIVE,
                handoff_sm.HandoffState.AGENT_OFFERED,
            ):
                # 已分配出去（含仅派出未接单）的工单：只有被分配坐席能关
                if conv is None:
                    raise HTTPException(409, detail="人工会话缺少会话记录")
                facade._assert_current_agent(conv.assigned_agent_id, agent_id)
            handoff_sm.transition(
                current, handoff_sm.HandoffState.CLOSED,
            )
            row.handoff_state = "closed"
            row.closed_at = datetime.now(timezone.utc)
            # 会话关闭前捕获（commit 后实例过期，作用域外不可再访问）
            handoff_user_id = row.user_id

            # 会话处理模式归位（human / waiting_human → ai）
            from sqlalchemy import update

            conv_update_where = [
                CSConversation.conversation_id == conversation_id,
                CSConversation.handling_mode != "ai",
            ]
            assignment_update_where = [
                CSAssignment.conversation_id == conversation_id,
                CSAssignment.unassigned_at.is_(None),
            ]
            if tenant_id is not None:
                conv_update_where.append(CSConversation.tenant_id == tenant_id)
                assignment_update_where.append(
                    CSAssignment.tenant_id == tenant_id
                )
            await db.execute(
                update(CSConversation)
                .where(*conv_update_where)
                .values(
                    handling_mode="ai",
                    assigned_agent_id=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.execute(
                update(CSAssignment)
                .where(*assignment_update_where)
                .values(unassigned_at=datetime.now(timezone.utc))
            )

            # P4 事件统一：conversation.closed 进 PG Outbox（与关闭同事务），
            # 广播由 relay 异步完成；WS event name 不变。
            from backend.customer_service.dispatch.outbox import append_event

            append_event(
                db,
                tenant_id=row.tenant_id,
                conversation_id=conversation_id,
                type="conversation.closed",
                payload={
                    "conversation_id": conversation_id,
                    "closed_by": agent_id,
                },
                handoff_id=row.handoff_id,
                actor_user_id=agent_id,
                now=datetime.now(timezone.utc),
            )

        # L1 缓存失效（缓存 key = (user_id, session_id)，handoff 行的
        # conversation_id 即 session_id，user_id 行上有）
        from backend.customer_service.handoff_store import (
            get_handoff_store,
        )

        get_handoff_store().invalidate(handoff_user_id, conversation_id)

        return {
            "conversation_id": conversation_id,
            "handoff_state": "closed",
            "closed_by": agent_id,
        }
    except HTTPException:
        raise
    except BusinessRuleError as e:
        raise HTTPException(409, detail=str(e))
    except Exception as e:
        logger.warning(f"[CSAdmin] close failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/claim")
async def claim_conversation(conversation_id: str, body: ClaimRequest, request: Request):
    """坐席认领会话：handoff → human_active。

    仅允许 waiting_human → human_active；handoff_requested 说明用户刚发起、
    尚未进入排队（由客服运行时流转），返回 409 让坐席稍后再认领。
    P1（audit-report §P0-4）：认领原子化 —— 单条条件 UPDATE
    （WHERE handoff_state='waiting_human'）+ 影响行数判定，并发双认领
    只有一方成功；agent_id 服务端解析。
    """
    agent_id, tenant_id = await facade._resolve_agent_identity_and_tenant(
        request, body.agent_id
    )

    from backend.customer_service.errors import BusinessRuleError

    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_claim(
            conversation_id, agent_id, tenant_id, run_sync
        )
    except HTTPException:
        raise
    except BusinessRuleError as e:
        raise HTTPException(409, detail=str(e))
    except Exception as e:
        logger.warning(f"[CSAdmin] claim failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/agent-messages", response_model=AgentMessageResponse)
async def post_agent_message(conversation_id: str, body: AgentMessageRequest, request: Request):
    """坐席发言：落库为 human_agent 消息（用户侧经消息增量接口/后续 SSE 可见）。

    P7：坐席身份由服务端从 ``cs_agents.auth_user_id`` 反查；只有该会话当前
    被分配的坐席能发言（``_async_agent_message`` 内的归属断言给 409）。
    """
    agent_id, tenant_id = await facade._resolve_agent_identity_and_tenant(
        request, body.agent_id
    )
    if not body.content.strip():
        raise HTTPException(422, detail="content is required")

    try:
        from backend.customer_service._db_loop import run_sync
        return await facade._async_agent_message(
            conversation_id, agent_id, tenant_id, body.content.strip(), run_sync
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] agent message failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/{conversation_id}/typing")
async def post_agent_typing(
    conversation_id: str, body: TypingRequest, request: Request,
):
    """坐席「输入中」上报（瞬态，双向输入中指示 · 坐席→用户方向）。

    高频轻量写：Redis SETEX 5s TTL（不可用降级进程内存），不落库不入
    事件流。用户侧经 /my/{id}/messages 轮询响应的 agent_typing 字段可见。
    鉴权走本路由组统一的 api-key 中间件（同 /agent-messages）。
    """
    facade._require_cs_operator(request)
    from backend.customer_service.typing_state import set_typing

    set_typing("agent", conversation_id)
    return {"conversation_id": conversation_id, "ok": True}


async def _async_handoff_queue(
    state_list, run_sync, tenant_id: str | None = None,
    assigned_agent_id: str | None = None,
):
    from sqlalchemy import select

    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        from backend.customer_service.repository import HandoffRepository

        repo = HandoffRepository(db)
        # STOP CS-A P0-2：队列按租户隔离（服务间通道跨租户）
        rows = await repo.list_open(
            states=state_list, tenant_id=tenant_id,
            assigned_agent_id=assigned_agent_id,
        )

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
                handoff_id=h.handoff_id,
                trigger_type=h.trigger_type,
                trigger_reason=h.trigger_reason,
                updated_at=h.updated_at.isoformat() if h.updated_at else "",
                last_message_preview=(
                    last_msg.content[:80] if last_msg else None
                ),
            ))
        return HandoffQueueResponse(items=items, total=len(items))


async def _async_claim(
    conversation_id: str, agent_id: str, tenant_id: str | None, run_sync,
):
    """认领会话并同步写入 handoff、conversation、assignment 三处状态。

    P3 事务统一：整个用例一个事务（begin → 退出提交/异常回滚），
    分支内不再手写 commit。

    2026-11-08 跨租户审计（cs-claim-close.missing-tenant-predicate）：
    ``tenant_id`` 非空时（JWT 坐席）所有 handoff/conversation 读写都带
    租户谓词，他租会话返回 404；API-Key 服务通道传 ``None`` 保持既有
    跨租户语义。
    """
    from datetime import datetime, timezone

    from sqlalchemy import select, update

    from backend.customer_service.models.handoff import CSHandoff
    from backend.memory.database import AsyncSessionLocal

    handoff_where = [
        CSHandoff.conversation_id == conversation_id,
        CSHandoff.handoff_state != "closed",
    ]
    if tenant_id is not None:
        handoff_where.append(CSHandoff.tenant_id == tenant_id)

    async with AsyncSessionLocal() as db, db.begin():
        row = (
            await db.execute(
                select(
                    CSHandoff.conversation_id,
                    CSHandoff.user_id,
                    CSHandoff.handoff_state,
                    CSHandoff.tenant_id,
                )
                .where(*handoff_where)
                .limit(1)
            )
        ).first()
        if row is None:
            raise HTTPException(404, detail="No open handoff for conversation")
        handoff_user_id = row.user_id
        current_state = row.handoff_state

        if current_state == "human_active":
            assigned_agent_id = await facade._load_assigned_agent_id(
                db, conversation_id, tenant_id,
            )
            if assigned_agent_id and assigned_agent_id != agent_id:
                raise HTTPException(409, detail="会话已由其他坐席认领")
            if not assigned_agent_id:
                # 修复历史上只写 human_active、未写 assignment 的脏状态。
                await facade._ensure_cs_agent(db, agent_id)
                from backend.customer_service.managers.conversation_manager import (
                    ConversationManager,
                )

                conv_mgr = ConversationManager(db)
                await conv_mgr.get_or_create(
                    conversation_id, handoff_user_id,
                    tenant_id=row.tenant_id,
                )
                await conv_mgr.assign_agent(
                    conversation_id, agent_id, assigned_by=agent_id,
                )
                # D1：脏状态修复同样要回填 handoffs.assigned_agent_id，
                # 否则 mine=true（handoffs 谓词）依旧查不到该会话。
                await db.execute(
                    update(CSHandoff)
                    .where(
                        CSHandoff.conversation_id == conversation_id,
                        CSHandoff.handoff_state != "closed",
                        *(
                            [CSHandoff.tenant_id == tenant_id]
                            if tenant_id is not None
                            else []
                        ),
                    )
                    .values(assigned_agent_id=agent_id)
                )
            return {
                "conversation_id": conversation_id,
                "handoff_state": current_state,
                "agent_id": agent_id,
                "already_claimed": True,
            }

        # 原子条件更新：只有仍处于 waiting_human 的行才会被认领。
        claim_where = [
            CSHandoff.conversation_id == conversation_id,
            CSHandoff.handoff_state == "waiting_human",
        ]
        if tenant_id is not None:
            claim_where.append(CSHandoff.tenant_id == tenant_id)
        claim_result = await db.execute(
            update(CSHandoff)
            .where(*claim_where)
            # D1（2026-11-22 实机验证）：认领必须同步写 handoffs.assigned_agent_id。
            # 之前只写 handoff_state，分配关系只落在 conversations 表，
            # mine=true 队列（handoff_repo 谓词查 handoffs 表）永远查不到，
            # 坐席端「我的会话」恒空、F5 刷新即丢。
            .values(handoff_state="human_active", assigned_agent_id=agent_id)
        )
        claimed = claim_result.rowcount > 0
        if not claimed:
            # 并发下已被认领 / 状态已流转 —— 重查给出明确响应。
            fresh_where = [
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            ]
            if tenant_id is not None:
                fresh_where.append(CSHandoff.tenant_id == tenant_id)
            fresh = (
                await db.execute(
                    select(CSHandoff.handoff_state)
                    .where(*fresh_where)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if fresh == "human_active":
                assigned_agent_id = await facade._load_assigned_agent_id(
                    db, conversation_id, tenant_id,
                )
                if assigned_agent_id and assigned_agent_id != agent_id:
                    raise HTTPException(409, detail="会话已由其他坐席认领")
                if not assigned_agent_id:
                    # 赢家可能已经提交 handoff 状态，但 assignment 尚未提交；
                    # 此时不能把竞态失败方误报为幂等成功，否则多个坐席都会
                    # 看到“认领成功”。等待下一次重试由已提交的 assignment 判定。
                    raise HTTPException(
                        409,
                        detail="会话正在被其他坐席认领，请稍后重试",
                    )
                return {
                    "conversation_id": conversation_id,
                    "handoff_state": fresh,
                    "agent_id": agent_id,
                    "already_claimed": True,
                }
            raise HTTPException(
                409,
                detail=(
                    f"Conversation handoff is {fresh or 'closed'}, "
                    "only waiting_human can be claimed"
                ),
            )

        await facade._ensure_cs_agent(db, agent_id)
        from backend.customer_service.managers.conversation_manager import (
            ConversationManager,
        )

        conv_mgr = ConversationManager(db)
        await conv_mgr.get_or_create(
            conversation_id, handoff_user_id,
            tenant_id=row.tenant_id,
        )
        await conv_mgr.escalate_to_human(
            conversation_id, agent_id, assigned_by=agent_id,
        )

        # P4 事件统一：conversation.claimed 进 PG Outbox（与认领同事务），
        # 广播由 relay 异步完成；WS event name 不变。
        from backend.customer_service.dispatch.outbox import append_event

        append_event(
            db,
            tenant_id=row.tenant_id,
            conversation_id=conversation_id,
            type="conversation.claimed",
            payload={
                "conversation_id": conversation_id,
                "agent_id": agent_id,
            },
            actor_user_id=agent_id,
            now=datetime.now(timezone.utc),
        )

    # L1 缓存失效：用户侧下个 turn 的 state loader 才能从 DB 读到
    # human_active（否则仍读缓存里的 waiting_human，回复话术滞后一档）。
    from backend.customer_service.handoff_store import get_handoff_store

    get_handoff_store().invalidate(handoff_user_id, conversation_id)

    return {
        "conversation_id": conversation_id,
        "handoff_state": "human_active",
        "agent_id": agent_id,
        "already_claimed": False,
    }


async def _async_agent_message(
    conversation_id: str, agent_id: str, tenant_id: str | None, content: str, run_sync,
):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from backend.customer_service.models.handoff import CSHandoff
    from backend.memory.database import AsyncSessionLocal

    handoff_where = [
        CSHandoff.conversation_id == conversation_id,
        CSHandoff.handoff_state != "closed",
    ]
    if tenant_id is not None:
        handoff_where.append(CSHandoff.tenant_id == tenant_id)

    # P3 事务统一：route 持有事务（begin → block 退出提交，异常回滚）
    async with AsyncSessionLocal() as db, db.begin():
        result = await db.execute(
            select(CSHandoff).where(*handoff_where).limit(1)
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
        conv, _ = await conv_mgr.get_or_create(
            conversation_id, row.user_id,
            tenant_id=row.tenant_id,
        )
        facade._assert_current_agent(conv.assigned_agent_id, agent_id)

        from backend.customer_service.managers.message_manager import MessageManager

        mgr = MessageManager(db)
        msg = await mgr.save_human_agent_message(
            conversation_id, content, sender_id=agent_id
        )

        # 提交前捕获字段（session expire_on_commit=False，此处为防御式快照）
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

        # P4 事件统一：message.created 进 PG Outbox（与消息落库同事务），
        # 广播由 relay 异步完成；WS event name / payload 不变。
        from backend.customer_service.dispatch.outbox import append_event

        append_event(
            db,
            tenant_id=row.tenant_id,
            conversation_id=conversation_id,
            type="message.created",
            payload={
                "conversation_id": conversation_id,
                "last_id": msg_pk,
                "message": msg_payload,
            },
            actor_user_id=agent_id,
            now=datetime.now(timezone.utc),
        )

    return AgentMessageResponse(
        message_id=msg.message_id,
        sender_type=msg.sender_type,
        content=msg.content,
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )

# 注册默认实现到 facade
for _fn in [
    _async_handoff_queue,
    _async_claim,
    _async_agent_message
]:
    facade.register(_fn.__name__, _fn)
