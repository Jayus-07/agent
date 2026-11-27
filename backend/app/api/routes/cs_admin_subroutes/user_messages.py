"""CS Admin 客户侧会话交互与消息发送子路由。"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from backend.app.api.routes.cs_admin_subroutes.base import facade, logger
from backend.app.api.routes.cs_admin_subroutes.schemas import *

router = APIRouter()

@router.get("/my", response_model=MyConversationsResponse)
async def list_my_conversations(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
):
    """用户侧「我的客服会话」（含消息）——客服抽屉刷新后恢复历史用。

    身份取网关验签后注入的头（与 /memory/* 同一 resolve_identity 模式），
    只返回当前登录用户自己的会话；guest 一律 401。
    必须注册在 GET /{conversation_id} 之前，否则 "my" 被当作 conversation_id。
    消息读本地库（与 get_conversation_messages 同一先例：读侧不随 java cutover 代理）。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    if not ident.authenticated:
        raise HTTPException(401, detail="未认证：客服会话按登录用户隔离")

    try:
        return await facade._async_my_conversations(
            user_id=ident.user_id, tenant_id=ident.tenant_id, limit=limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] list_my_conversations failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/my/{conversation_id}/messages")
async def post_my_message(
    conversation_id: str, request: Request, body: MyMessageBody,
) -> dict:
    """用户在人工服务态直接发消息给坐席（cs-workstation-v2 P3）。

    目标态变更：此前人工接管后用户输入被图内拦截（排队话术作答），
    现放开为「转人工后（waiting_human/agent_offered/human_active）消息
    直达坐席」——落库 sender_type=user 并广播 message.created（与图 turn
    同契约，坐席 WS / 消息轮询均可消费）。AI 态（ai_active/none）不走
    本端点，仍走正常聊天流；已关闭会话 409。

    P8：写副作用收口全局幂等（Idempotency-Key 必填）——同键同内容重放
    返回首次结果（不重复落库/广播），同键不同内容 409。归属/状态闸在
    幂等边界外（安全语义优先：重放窗口内会话状态已变更的按新状态拒绝）。
    """
    await facade._ensure_my_conversation(request, conversation_id)

    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not user_id or not getattr(ident, "authenticated", False):
        raise HTTPException(401, detail="未认证：请登录后发送消息")
    if not tenant_id:
        raise HTTPException(403, detail="发送消息需要有效租户身份")

    content = body.content.strip()
    if not content:
        raise HTTPException(422, detail="消息内容不能为空")
    # M2：附件非空 → card 落库；条数与单条体积设上限防滥用
    msg_attachments = body.attachments or None
    if msg_attachments and len(msg_attachments) > 4:
        raise HTTPException(422, detail="附件数量超出上限")
    msg_content_type = "card" if msg_attachments else "text"

    from datetime import datetime, timezone

    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        handoff = load_active_handoff_sync(
            tenant_id, conversation_id, raise_on_error=True,
        )
    except Exception as exc:
        raise HTTPException(503, detail="人工服务状态暂不可核验，请稍后重试") from exc
    handoff_state = str((handoff or {}).get("handoff_state") or "none")
    if handoff_state in ("none", "ai_active", "closed"):
        raise HTTPException(
            409,
            detail="当前为 AI 服务态，请直接在对话中发送消息；已结束会话不可再发送",
        )

    from backend.app.api.deps import run_authenticated_mutation
    from backend.customer_service._db_loop import run_sync
    from backend.customer_service.realtime import get_agent_hub

    now = datetime.now(timezone.utc)

    def _persist_and_notify() -> dict:
        """幂等副作用（线程池内执行）：落库经 cs-db loop 桥（AsyncSessionLocal
        按 loop 注册引擎，跨 loop 安全），广播为线程安全 fire-and-forget。

        桥超时（10s）≠ 副作用未发生——协程可能仍在途并最终提交。映射为
        SideEffectOutcomeUnknown 让 executor 落 UNCERTAIN 保守阻断同键重执
        （普通 FAILED 会放行重试 → 与在途协程各插一行，重复消息）；同时
        cancel_on_timeout 中止在途协程、尽快归还连接。与 executor 的
        crash-window「终态未知禁止自动重试」同一保守语义。
        """
        import concurrent.futures

        from backend.shared.idempotency import SideEffectOutcomeUnknown

        try:
            message_id = run_sync(
                facade._async_save_my_message(
                    conversation_id, user_id, tenant_id, content, now,
                    content_type=msg_content_type,
                    attachments=msg_attachments,
                ),
                cancel_on_timeout=True,
            )
        except concurrent.futures.TimeoutError as exc:
            raise SideEffectOutcomeUnknown(
                "消息落库桥超时，副作用终态未知"
            ) from exc
        # 与 conversation_store._msg_payload 同构：坐席 WS/轮询零改动消费
        get_agent_hub().publish(
            "message.created",
            persist=True,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            last_id=int(message_id) if str(message_id).isdigit() else None,
            message={
                "message_id": str(message_id),
                "sender_type": "user",
                "content": content,
                "content_type": msg_content_type,
                "attachments": msg_attachments,
                "created_at": now.isoformat(),
            },
        )
        return {
            "message_id": str(message_id),
            "handoff_state": handoff_state,
            "created_at": now.isoformat(),
        }

    from starlette.concurrency import run_in_threadpool

    try:
        # Redis claim/ping 为同步 IO，放线程池避免阻塞事件循环
        return await run_in_threadpool(
            run_authenticated_mutation,
            request,
            "cs_my_message_send",
            {
                "conversation_id": conversation_id,
                "content": content,
                "attachments": msg_attachments,
            },
            _persist_and_notify,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] post_my_message persist failed: {e}")
        raise HTTPException(503, detail="消息发送失败，请稍后重试") from e


@router.get("/my/{conversation_id}/pending")
async def get_my_pending_action(request: Request, conversation_id: str) -> dict:
    """读取当前用户会话的权威待确认快照，仅投影 UI 确认所需的安全字段。"""
    await facade._ensure_my_conversation(request, conversation_id)

    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    user_id = (getattr(ident, "user_id", "") or "").strip()
    tenant_id = (getattr(ident, "tenant_id", "") or "").strip()
    if not user_id or not getattr(ident, "authenticated", False):
        raise HTTPException(401, detail="未认证：请登录后访问会话")
    if not tenant_id:
        raise HTTPException(403, detail="待确认状态需要有效租户身份")

    from backend.customer_service.confirmation_store import (
        StoreWriteError,
        get_confirmation_store,
    )
    from backend.customer_service.handoff.lifecycle import load_active_handoff_sync

    try:
        pending = get_confirmation_store().load_authoritative(
            user_id, conversation_id, tenant_id,
        )
    except StoreWriteError as exc:
        raise HTTPException(503, detail="待确认状态暂不可核验，请稍后重试") from exc
    if pending is None:
        return {"pending_action": None}

    try:
        handoff = load_active_handoff_sync(
            tenant_id, conversation_id, raise_on_error=True,
        )
    except Exception as exc:
        raise HTTPException(503, detail="人工服务状态暂不可核验，请稍后重试") from exc

    from datetime import datetime, timezone

    from backend.shared.pii_mask import mask_pii

    raw_target = str(pending.get("target_id") or "").strip()
    target_type = str(pending.get("target_type") or "业务对象").strip()
    target_label = {
        "order": "订单",
        "order_id": "订单",
        "ticket": "工单",
        "refund": "退款申请",
        "return": "退货申请",
    }.get(target_type.lower(), "业务对象")
    if raw_target:
        masked_target = (
            f"{target_label}尾号 ****{raw_target[-4:]}"
            if len(raw_target) > 4
            else target_label
        )
    else:
        masked_target = target_label

    raw_summary = str(pending.get("proposal_text") or "待确认的业务操作")
    if raw_target:
        raw_summary = raw_summary.replace(raw_target, masked_target)
    summary, _ = mask_pii(raw_summary)

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
            # 不可解析的过期时间无法支持安全确认，前端按失效状态处理。
            expired = True

    handoff_state = str((handoff or {}).get("handoff_state") or "")
    if handoff_state in {
        "handoff_requested", "waiting_human", "agent_offered", "human_active",
    }:
        state = "paused_handoff"
    else:
        state = "expired" if expired else "pending"

    # need_info 提案（缺订单号等槽位）不可确认：后端权威判定，前端只消费
    # confirmable，避免把「等待补充信息」渲染成可执行的确认卡（2026-11-03）。
    missing_slots = pending.get("missing_slots")
    confirmable = (
        pending.get("requires_confirmation") is not False
        and not (isinstance(missing_slots, list) and missing_slots)
        and str(pending.get("status") or "") != "need_info"
    )

    action_type = str(pending.get("action_type") or "unknown")
    refund_review_mode = None
    if action_type == "refund_request":
        from backend.config import customer_service as cfg

        refund_mode = str(getattr(cfg, "CS_REFUND_REQUEST_MODE", "disabled")).lower()
        gateway_mode = str(getattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "")).lower()
        http_mode = gateway_mode == "http" and refund_mode == "http"
        sandbox_mode = gateway_mode == "sandbox" and refund_mode == "sandbox"
        if http_mode or sandbox_mode:
            refund_review_mode = refund_mode
        else:
            confirmable = False

    return {
        "pending_action": {
            "proposal_id": str(pending.get("proposal_id") or pending.get("action_id") or ""),
            "version": int(pending.get("version") or pending.get("proposal_version") or 1),
            "action_type": str(pending.get("action_type") or "unknown"),
            "masked_target": masked_target,
            "summary": summary,
            "reason": str(
                ((pending.get("after_state") or {}).get("reason") or "")
            ),
            "expires_at": expires_at,
            "state": state,
            "confirmable": confirmable,
            **({"refund_review_mode": refund_review_mode} if refund_review_mode else {}),
        },
    }


@router.get("/my/{conversation_id}/messages", response_model=HandoffMessagesResponse)
async def get_my_conversation_messages(
    request: Request,
    conversation_id: str,
    since_id: int = Query(0, ge=0, description="只返回 id > since_id 的消息"),
    limit: int = Query(100, ge=1, le=200),
):
    """用户侧消息增量拉取（P3.4 端点拆分）。

    与坐席端 /{conversation_id}/messages 的差异：
    - 身份：resolve_identity 登录态强制（401 拒 guest）——坐席端走
      api-key/坐席 JWT（_ensure_conversation_access 的 guest 匿名语义不适用）
    - 归属：只读本人的会话（user_id 精确匹配，非 guest 匿名放行）
    路径注册在 /{conversation_id}/* 之后无冲突（3 段 vs 2 段）。
    响应附带 agent_typing（坐席「输入中」瞬态信号，TTL 5s）。
    """
    await facade._ensure_my_conversation(request, conversation_id)

    try:
        from backend.customer_service._db_loop import run_sync
        result = await facade._async_messages_since(
            conversation_id, since_id, limit, run_sync
        )
        from backend.customer_service.typing_state import is_typing
        result.agent_typing = is_typing("agent", conversation_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] my-messages since failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


@router.post("/my/{conversation_id}/typing")
async def post_my_typing(request: Request, conversation_id: str):
    """用户「输入中」上报（瞬态，双向输入中指示 · 用户→坐席方向）。

    归属校验同 /my/messages；状态写 Redis TTL 5s，并经 AgentHub 广播
    user.typing 瞬态事件（persist=False 不落库），坐席 WS 实时可见。
    """
    await facade._ensure_my_conversation(request, conversation_id)

    from backend.app.api.identity import resolve_identity
    from backend.customer_service.typing_state import set_typing

    set_typing("user", conversation_id)

    ident = resolve_identity(request)
    from backend.customer_service.realtime import get_agent_hub

    # STOP CS-A P0-2/B3：瞬态事件信封带租户，坐席侧按租户过滤投递
    get_agent_hub().publish(
        "user.typing",
        conversation_id=conversation_id,
        tenant_id=ident.tenant_id,
        persist=False,
    )
    return {"conversation_id": conversation_id, "ok": True}


async def _async_save_my_message(
    conversation_id: str, user_id: str, tenant_id: str, content: str, now,
    content_type: str = "text",
    attachments: list | None = None,
) -> str:
    from sqlalchemy import select

    from backend.customer_service.managers.message_manager import MessageManager
    from backend.customer_service.models.handoff import CSHandoff
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db, db.begin():
        # D2（2026-11-22 实机验证）：同一会话经多轮转人工会有多条 handoff 行，
        # 裸 scalar_one_or_none() 抛 MultipleRows → 人工态发消息 503。
        # 归属校验取最新一条（同会话各 handoff 行 user_id 一致）。
        handoff = (
            await db.execute(
                select(CSHandoff)
                .where(
                    CSHandoff.tenant_id == tenant_id,
                    CSHandoff.conversation_id == conversation_id,
                )
                .order_by(CSHandoff.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        # 归属二次校验：handoff.user_id 必须与当前用户一致（防跨用户投递）
        if handoff is not None and str(handoff.user_id) != str(user_id):
            raise HTTPException(403, detail="无权向该会话发送消息")
        mgr = MessageManager(db)
        msg = await mgr.save_user_message(
            conversation_id, content,
            content_type=content_type,
            attachments=attachments,
        )
        return msg.id


async def _async_my_conversations(
    *, user_id: str, tenant_id: str, limit: int,
) -> MyConversationsResponse:
    """当前用户的最近会话（含消息，单会话上限 100 条）。"""
    from sqlalchemy import and_, select

    from backend.customer_service.models.conversation import CSConversation
    from backend.customer_service.models.handoff import CSHandoff
    from backend.customer_service.models.message import CSMessage
    from backend.memory.database import AsyncSessionLocal

    # 坐席关单会把处理模式交回 AI，只有用户尚未继续对话时才显示该次服务已结束。
    has_new_user_turn = (
        select(CSMessage.id)
        .where(
            CSMessage.conversation_id == CSHandoff.conversation_id,
            CSMessage.sender_type == "user",
            CSMessage.created_at > CSHandoff.closed_at,
        )
        .correlate(CSHandoff)
        .exists()
    )
    latest_handoff_closed = (
        select(and_(CSHandoff.handoff_state == "closed", ~has_new_user_turn))
        .where(
            CSHandoff.conversation_id == CSConversation.conversation_id,
            CSHandoff.tenant_id == CSConversation.tenant_id,
            CSHandoff.user_id == CSConversation.user_id,
        )
        .order_by(CSHandoff.created_at.desc(), CSHandoff.id.desc())
        .limit(1)
        .scalar_subquery()
    )

    async with AsyncSessionLocal() as db:
        conv_q = (
            select(CSConversation, latest_handoff_closed)
            .where(CSConversation.user_id == user_id)
            .order_by(CSConversation.last_activity_at.desc().nulls_last())
            .limit(limit)
        )
        # STOP CS-A P0-2：user_id 全局唯一是假设不是约束 —— 租户谓词显式化
        if tenant_id:
            conv_q = conv_q.where(CSConversation.tenant_id == tenant_id)
        convs = (await db.execute(conv_q)).all()
        if not convs:
            return MyConversationsResponse(items=[])

        ids = [c.conversation_id for c, _ in convs]
        msgs = (
            (
                await db.execute(
                    select(CSMessage)
                    .where(CSMessage.conversation_id.in_(ids))
                    .order_by(CSMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )

        by_conv: dict[str, list] = {}
        for m in msgs:
            bucket = by_conv.setdefault(m.conversation_id, [])
            if len(bucket) < 100:  # 单会话消息上限，防御异常长会话
                bucket.append(m)

        items = [
            MyConversationItem(
                conversation_id=c.conversation_id,
                summary=c.summary,
                conversation_status=c.conversation_status,
                latest_handoff_closed=bool(handoff_closed),
                handling_mode=c.handling_mode,
                created_at=c.created_at.isoformat() if c.created_at else "",
                last_activity_at=(
                    c.last_activity_at.isoformat() if c.last_activity_at else None
                ),
                messages=[
                    MyConversationMessage(
                        message_id=m.message_id,
                        sender_type=m.sender_type,
                        content=m.content,
                        content_type=m.content_type or "text",
                        attachments=m.attachments,
                        created_at=m.created_at.isoformat() if m.created_at else "",
                    )
                    for m in by_conv.get(c.conversation_id, [])
                ],
            )
            for c, handoff_closed in convs
        ]
        return MyConversationsResponse(items=items)

# 注册默认实现到 facade
for _fn in [
    _async_save_my_message,
    _async_my_conversations
]:
    facade.register(_fn.__name__, _fn)
