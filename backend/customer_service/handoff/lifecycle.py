"""handoff/lifecycle.py — 人工转接业务状态迁移的唯一应用服务（STOP CS-A P0-6）。

原则冻结（审计 2026-10-06 §六/§七）：

    PostgreSQL = Handoff Business State 唯一事实源
    LangGraph State / 前端 / WS = projection
    L1（HandoffStore 内存缓存）= cache only，禁止参与关键判定

此前图内两条创建路径（HandoffExpert / ComplaintExpert）经 HandoffStore
只写 ``handoffs`` 行，**不维护 ``conversations.handling_mode``**——同一 PG
里出现 handoffs=waiting_human + handling_mode=ai 的口径分叉。本模块把
「handoffs.state / conversations.handling_mode / assigned_agent /
assignment_version / outbox event」五个字段的维护收敛到一个入口：

- :func:`enter_waiting_handoff` —— 图内入池（ai_active → waiting_human，
  含首次建行），与 dispatch ``_create_in_transaction`` 同字段集；
- :func:`transition_handoff` —— 已有工单的状态迁移（自愈/关闭等），
  调用方先按全局锁序（conversations → handoffs）取行锁，本函数在同一
  事务内完成校验 + 五字段写 + outbox 事件。

Dispatch 侧（dispatch_once / accept / decline / reassign / reaper 的
offer 生命周期写）此前已逐处维护全部字段（审计确认无双写缺陷），保持
原状不改写；本模块与其共享同一套 repository 锁原语与状态机，作为
「图内写」与「恢复写」的唯一入口。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.errors import BusinessRuleError
from backend.customer_service.handoff import HandoffState, transition as sm_transition
from backend.customer_service.handoff.dispatch import outbox, repository
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.handoff import CSHandoff
from backend.shared.logger import logger

# handoff 状态 → conversations.handling_mode 投影（与 dispatch 各写点同映射）
HANDOFF_TO_HANDLING = {
    HandoffState.WAITING_HUMAN.value: "waiting_human",
    HandoffState.AGENT_OFFERED.value: "waiting_human",
    HandoffState.HUMAN_ACTIVE.value: "human",
    HandoffState.HANDOFF_REQUESTED.value: "waiting_human",
    HandoffState.AI_ACTIVE.value: "ai",
    HandoffState.CLOSED.value: "ai",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def handling_mode_for(handoff_state: str) -> str:
    return HANDOFF_TO_HANDLING.get(handoff_state, "ai")


def lock_conversation_stmt(*, conversation_id: str):
    """图内路径按 conversation_id 锁会话行（tenant 由调用方保证同源）。"""
    return (
        select(CSConversation)
        .where(CSConversation.conversation_id == conversation_id)
        .with_for_update()
        .limit(1)
    )


async def enter_waiting_handoff(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation_id: str,
    user_id: str,
    trigger_type: str,
    trigger_reason: str,
    ticket_id: str,
    now: datetime | None = None,
) -> CSHandoff:
    """图内入池：确保会话行存在 → 建 waiting_human 工单 → 更新会话投影。

    与 dispatch ``_create_in_transaction`` 维护同一字段集；区别仅在来源
    （AI 域图触发 vs 用户显式点按钮）。``total_deadline_at`` 与 dispatch
    入池同源（CS_HANDOFF_TIMEOUT_SECONDS），保证图内工单同样受 reaper
    总期限兜底——此前图内工单无期限，无人接单时永久卡在 waiting_human。
    幂等：同会话已有未关闭工单时直接复用（不抛错，与 store.load 复用
    语义一致，调用方一般已先行判重）。
    """
    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
    from backend.customer_service.managers.conversation_manager import (
        ConversationManager,
    )

    now = now or _now()
    existing = (
        await session.execute(
            select(CSHandoff)
            .where(
                CSHandoff.tenant_id == tenant_id,
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    # 会话行可能尚不存在（turn 落库是 fire-and-forget）——同事务幂等补齐，
    # tenant 与本入口显式同源（P0-2：缺租户由 ConversationManager fail-closed）
    conv_mgr = ConversationManager(session)
    conversation, _ = await conv_mgr.get_or_create(
        conversation_id, user_id, tenant_id=tenant_id,
    )

    handoff = CSHandoff(
        handoff_id=handoff_id(),
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        handoff_state=HandoffState.WAITING_HUMAN.value,
        trigger_type=trigger_type,
        trigger_reason=trigger_reason,
        ticket_id=ticket_id,
        priority=50,
        total_deadline_at=now + timedelta(seconds=CS_HANDOFF_TIMEOUT_SECONDS),
        created_at=now,
        updated_at=now,
    )
    session.add(handoff)

    conversation.handling_mode = HANDOFF_TO_HANDLING[HandoffState.WAITING_HUMAN.value]
    conversation.updated_at = now
    conversation.last_activity_at = conversation.last_activity_at or now
    await session.flush()
    return handoff


def handoff_id() -> str:
    import uuid

    return uuid.uuid4().hex


async def transition_handoff(
    session: AsyncSession,
    *,
    tenant_id: str,
    handoff: CSHandoff,
    conversation: CSConversation | None,
    target_state: str,
    clear_assigned_agent: bool = False,
    bump_assignment_version: bool = False,
    release_assignments: str | None = None,
    clear_offer_fields: bool = False,
    closed_reason: str | None = None,
    event_type: str | None = None,
    event_payload: dict | None = None,
    event_target_agent_id: str | None = None,
    actor_user_id: str = "system",
    now: datetime | None = None,
) -> CSHandoff:
    """唯一状态迁移入口（调用方持锁、本函数不取锁）。

    同一事务内维护：handoffs.state（状态机校验）/ assigned_agent_id /
    assignment_version / 活动 assignment 终结 / conversations.handling_mode
    投影 / outbox 事件。非法迁移抛 BusinessRuleError（整事务回滚）。
    """
    now = now or _now()
    current = HandoffState(handoff.handoff_state)
    target = HandoffState(target_state)
    sm_transition(current, target)

    handoff.handoff_state = target.value
    handoff.updated_at = now
    if clear_assigned_agent:
        handoff.assigned_agent_id = None
    if bump_assignment_version:
        handoff.assignment_version = int(handoff.assignment_version or 0) + 1
    if clear_offer_fields:
        handoff.offered_at = None
        handoff.offer_expires_at = None
    if closed_reason is not None:
        handoff.closed_reason = closed_reason
    if target is HandoffState.CLOSED:
        handoff.closed_at = now

    if release_assignments is not None:
        released = await repository.lock_active_assignments_for_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff.handoff_id,
        )
        for assignment in released:
            assignment.state = release_assignments
            assignment.unassigned_at = now

    if conversation is not None:
        conversation.handling_mode = handling_mode_for(target.value)
        conversation.updated_at = now
        if clear_assigned_agent:
            conversation.assigned_agent_id = None

    if event_type:
        outbox.append_event(
            session,
            tenant_id=tenant_id,
            conversation_id=handoff.conversation_id,
            type=event_type,
            payload={
                "conversation_id": handoff.conversation_id,
                "handoff_id": handoff.handoff_id,
                "tenant_id": tenant_id,
                "previous_state": current.value,
                "handoff_state": target.value,
                **(event_payload or {}),
            },
            handoff_id=handoff.handoff_id,
            target_agent_id=event_target_agent_id,
            actor_user_id=actor_user_id,
            now=now,
        )

    await session.flush()
    logger.info(
        "[HandoffLifecycle] %s: %s → %s (conv=%s, version=%s)",
        actor_user_id, current.value, target.value,
        handoff.conversation_id, handoff.assignment_version,
    )
    return handoff


# ── 图内（sync 节点）sync 桥 ────────────────────────────────────


def enter_waiting_handoff_sync(
    *,
    tenant_id: str,
    conversation_id: str,
    user_id: str,
    trigger_type: str,
    trigger_reason: str,
    ticket_id: str,
) -> dict[str, Any]:
    """HandoffExpert/ComplaintExpert 的 sync 桥（graph 线程 → _db_loop）。

    替代旧 ``HandoffStore.save`` 两跳写：单事务建行 + 会话投影同写，
    返回 ``{"handoff_id", "handoff_state", "updated_at"}`` 供专家组装
    审计与实时事件。写失败在严格模式抛 StoreWriteError（与旧 Store 同
    语义，工单不持久化就必须失败）。
    """
    from backend.customer_service._db_loop import run_sync

    async def _op() -> dict[str, Any]:
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session, session.begin():
            handoff = await enter_waiting_handoff(
                session,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=user_id,
                trigger_type=trigger_type,
                trigger_reason=trigger_reason,
                ticket_id=ticket_id,
            )
            return {
                "handoff_id": handoff.handoff_id,
                "handoff_state": handoff.handoff_state,
                "updated_at": (
                    handoff.updated_at.isoformat() if handoff.updated_at else ""
                ),
            }

    try:
        return run_sync(_op())
    except BusinessRuleError:
        raise
    except Exception as exc:
        from datetime import datetime, timezone as _tz

        from backend.customer_service.confirmation_store import (
            StoreWriteError,
            _strict_writes,
        )
        from backend.observability.metrics import record_cs_store_db_failure

        logger.error(
            "[HandoffLifecycle] enter_waiting_handoff failed: %s", exc, exc_info=True,
        )
        try:
            record_cs_store_db_failure("handoff", "enter_waiting")
        except Exception:
            logger.debug("[HandoffLifecycle] metrics unavailable")
        if _strict_writes():
            # 严格模式（生产默认）：工单不持久化就必须失败
            raise StoreWriteError("HandoffStore", "enter_waiting") from exc
        # 非严格模式（单测/无 DB 调试）：与旧 HandoffStore.save 同语义，
        # 返回内存态快照让专家流程继续（不阻断安抚回复）
        return {
            "handoff_id": "",
            "handoff_state": HandoffState.WAITING_HUMAN.value,
            "updated_at": datetime.now(_tz.utc).isoformat(),
        }


def load_active_handoff_sync(
    tenant_id: str, conversation_id: str,
) -> dict[str, Any] | None:
    """图内按会话直读 PG 活跃工单（sync 桥；L1 不参与）。"""
    from backend.customer_service._db_loop import run_sync

    async def _op() -> dict[str, Any] | None:
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(CSHandoff)
                    .where(
                        CSHandoff.tenant_id == tenant_id,
                        CSHandoff.conversation_id == conversation_id,
                        CSHandoff.handoff_state != "closed",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "handoff_id": row.handoff_id,
            "handoff_state": row.handoff_state,
            "ticket_id": row.ticket_id,
            "trigger_type": row.trigger_type,
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }

    try:
        return run_sync(_op())
    except Exception as exc:
        logger.warning("[HandoffLifecycle] load_active_handoff failed: %s", exc)
        return None
