"""dispatch/reaper.py — 过期 offer 回收与工单终态（P7）。

状态机里由**时间**驱动的两条边（方案 §五）：

    agent_offered ──30 秒超时──▶ waiting_human         （attempt 未用尽）
    agent_offered ──超过 5 次或 600 秒──▶ closed       （通知用户并恢复 AI）
    waiting_human ──超过 600 秒──▶ closed              （P6 刻意跳过的 Q5 归此）

为什么必须每秒跑：方案 §六 P7 完成标准要求「过期后 2 秒内释放并重派」。
worker 的 tick 间隔是 1 秒，reaper 排在同 tick 派单之前，因此
「回收 → 同一 tick 立刻重新派单」在正常情况下是同秒完成。

多副本安全：候选无锁读出，逐行按 ``conversations → handoffs`` 顺序取锁
（与 P4 入池、P6 派单、P7 接单完全同序），锁内复核状态，拿不到锁
（``SKIP LOCKED``）就跳过等下一轮。**不使用任何 Redis 协调**。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.cs_dispatch import (
    CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS,
    CS_MAX_DISPATCH_ATTEMPTS,
    CS_REAPER_BATCH_LIMIT,
)
from backend.customer_service.handoff.dispatch import (
    agent_busy,
    outbox,
    presence,
    repository,
)
from backend.shared.logger import logger

EVENT_OFFER_EXPIRED = "conversation.offer_expired"
EVENT_HANDOFF_CLOSED = "conversation.handoff_closed"
EVENT_HUMAN_ACTIVE_RECOVERED = "conversation.human_active_recovered"

REASON_OFFER_TIMEOUT = "offer_timeout"
REASON_MAX_ATTEMPTS = "max_attempts"
REASON_TOTAL_DEADLINE = "total_deadline"
REASON_AGENT_OFFLINE = "agent_offline_recovery"


@dataclass(frozen=True)
class ReapResult:
    """一轮 reaper 的结果（worker 观测与测试共用）。"""

    scanned: int = 0
    released: int = 0
    closed: int = 0
    contended: int = 0
    recovered: int = 0  # STOP CS-A P0-5：human_active 离线自愈送回重派数


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _terminal_reason(handoff, now: datetime) -> str | None:
    """工单是否该走终态关闭；返回原因，留在队列则返回 ``None``。"""
    if int(handoff.attempt_count or 0) >= CS_MAX_DISPATCH_ATTEMPTS:
        return REASON_MAX_ATTEMPTS
    if handoff.total_deadline_at is not None and handoff.total_deadline_at <= now:
        return REASON_TOTAL_DEADLINE
    return None


async def _release_assignment(
    session: AsyncSession,
    *,
    tenant_id: str,
    handoff_id: str,
    state: str,
    now: datetime,
) -> list[str]:
    """把该工单的活动 assignment 逐条终结；返回被解除的坐席 ID。"""
    released_agents: list[str] = []
    for assignment in await repository.lock_active_assignments_for_handoff(
        session, tenant_id=tenant_id, handoff_id=handoff_id
    ):
        assignment.state = state
        assignment.unassigned_at = now
        if assignment.agent_id:
            released_agents.append(str(assignment.agent_id))
    return released_agents


async def _close_handoff(
    session: AsyncSession,
    *,
    tenant_id: str,
    handoff,
    conversation,
    reason: str,
    now: datetime,
) -> None:
    """终态关闭：工单 closed、会话恢复 AI、活动 assignment 终结、广播事件。"""
    released_agents = await _release_assignment(
        session,
        tenant_id=tenant_id,
        handoff_id=handoff.handoff_id,
        state="expired",
        now=now,
    )

    previous_agent_id = handoff.assigned_agent_id
    handoff.handoff_state = "closed"
    if reason == REASON_TOTAL_DEADLINE:
        # 池空/无人接单到达总等待期：给用户「留言兜底」语义而不是冷冰冰的超时
        handoff.closed_reason = (
            "total_deadline:人工坐席繁忙，已为您保留会话记录，客服稍后会主动联系您"
        )
    else:
        handoff.closed_reason = f"{reason}:超时未接单，已恢复 AI 服务"
    handoff.closed_at = now
    handoff.updated_at = now
    handoff.assigned_agent_id = None
    handoff.offered_at = None
    handoff.offer_expires_at = None

    if conversation is not None:
        # 「通知用户并恢复 AI」——会话回到 AI 处理，坐席归属清空。
        conversation.handling_mode = "ai"
        conversation.assigned_agent_id = None
        conversation.updated_at = now

    outbox.append_event(
        session,
        tenant_id=tenant_id,
        conversation_id=handoff.conversation_id,
        type=EVENT_HANDOFF_CLOSED,
        payload={
            "conversation_id": handoff.conversation_id,
            "handoff_id": handoff.handoff_id,
            "tenant_id": tenant_id,
            "reason": reason,
            "notify_user": True,
            "resume_ai": True,
            "previous_agent_id": previous_agent_id,
            "released_agent_ids": released_agents,
            "attempt_count": int(handoff.attempt_count or 0),
        },
        handoff_id=handoff.handoff_id,
        actor_user_id="cs_reaper",
        now=now,
    )


async def _release_offer(
    session: AsyncSession,
    *,
    tenant_id: str,
    handoff,
    conversation,
    now: datetime,
) -> None:
    """把过期 offer 放回等待队列（attempt 预算未用尽）。"""
    released_agents = await _release_assignment(
        session,
        tenant_id=tenant_id,
        handoff_id=handoff.handoff_id,
        state="expired",
        now=now,
    )

    previous_agent_id = handoff.assigned_agent_id
    handoff.handoff_state = "waiting_human"
    handoff.assigned_agent_id = None
    handoff.offered_at = None
    handoff.offer_expires_at = None
    handoff.updated_at = now

    if conversation is not None:
        conversation.assigned_agent_id = None
        conversation.handling_mode = "waiting_human"
        conversation.updated_at = now

    # 审查 #4：超时回收与主动拒单同语义 —— 计入自动置忙计数
    # （record_agent_reject 自身 fail-open，Redis 故障不影响回收事务）。
    # 指标不在这里打点：提交由 reap_stage 完成，run_tick 在提交后统一
    # 记 record_cs_reaped，避免回滚路径指标超报/双计（审查 #17）。
    for agent_id in released_agents:
        await agent_busy.record_agent_reject(tenant_id, agent_id)

    outbox.append_event(
        session,
        tenant_id=tenant_id,
        conversation_id=handoff.conversation_id,
        type=EVENT_OFFER_EXPIRED,
        payload={
            "conversation_id": handoff.conversation_id,
            "handoff_id": handoff.handoff_id,
            "tenant_id": tenant_id,
            "reason": REASON_OFFER_TIMEOUT,
            # 当事坐席据此在本工单上进入冷却期（dispatcher 侧排除）
            "previous_agent_id": previous_agent_id,
            "released_agent_ids": released_agents,
            "attempt_count": int(handoff.attempt_count or 0),
        },
        handoff_id=handoff.handoff_id,
        actor_user_id="cs_reaper",
        now=now,
    )


async def reap_expired_offers(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> ReapResult:
    """回收过期 offer：能重派就回队列，预算用尽就终态关闭。"""
    candidates = await repository.list_expired_offer_candidates(
        session, now=now, limit=limit
    )
    released = closed = contended = 0

    for tenant_id, handoff_id, conversation_id in candidates:
        # 加锁顺序必须是 conversations → handoffs（与 P4 入池、P6 派单、
        # P7 接单同序）；候选阶段的无锁读只用来拿到 conversation_id 定序。
        conversation = await repository.lock_conversation(
            session, tenant_id=tenant_id, conversation_id=conversation_id
        )
        if conversation is None:
            contended += 1
            continue

        handoff = await repository.lock_expired_offer_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff_id, now=now
        )
        if handoff is None:
            contended += 1
            continue

        reason = _terminal_reason(handoff, now)
        if reason is None:
            await _release_offer(
                session,
                tenant_id=tenant_id,
                handoff=handoff,
                conversation=conversation,
                now=now,
            )
            released += 1
        else:
            await _close_handoff(
                session,
                tenant_id=tenant_id,
                handoff=handoff,
                conversation=conversation,
                reason=reason,
                now=now,
            )
            closed += 1

    return ReapResult(
        scanned=len(candidates),
        released=released,
        closed=closed,
        contended=contended,
    )


async def reap_overdue_waiting(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> ReapResult:
    """关闭超过总等待期的排队工单（P6 只跳过、不关闭的 Q5 终态）。"""
    candidates = await repository.list_overdue_waiting_candidates(
        session, now=now, limit=limit
    )
    closed = contended = 0

    for tenant_id, handoff_id, conversation_id in candidates:
        conversation = await repository.lock_conversation(
            session, tenant_id=tenant_id, conversation_id=conversation_id
        )
        if conversation is None:
            contended += 1
            continue

        handoff = await repository.lock_overdue_waiting_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff_id, now=now
        )
        if handoff is None:
            contended += 1
            continue
        await _close_handoff(
            session,
            tenant_id=tenant_id,
            handoff=handoff,
            conversation=conversation,
            reason=REASON_TOTAL_DEADLINE,
            now=now,
        )
        closed += 1

    return ReapResult(scanned=len(candidates), closed=closed, contended=contended)


async def reap_stale_human_active(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
) -> ReapResult:
    """STOP CS-A P0-5：human_active 坐席离线自愈。

    条件（全部满足才恢复）：
      handoff_state == human_active
      AND assigned 坐席 presence 离线（Redis，fail-closed：查不了不恢复）
      AND 最后一条坐席消息/工单更新距今超过 CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS
      AND 锁内 CAS（state + assigned_agent_id + assignment_version）仍成立
      AND 锁后复核 grace 期内没有坐席新消息（防「判掉线瞬间坐席正在回复」）

    恢复行为（E2）：human_active → waiting_human，清理 assignment（released，
    不排除该坐席本单——恢复上线可重新认领）、assignment_version+1、
    total_deadline 顺延一个完整窗口，dispatcher 下一 tick 自动重派。
    不直接落 ai_active：用户主动进入人工服务后优先继续找人工；重派预算
    耗尽后由既有 reaper 终态关闭并通知用户。
    """
    from backend.customer_service.handoff.lifecycle import transition_handoff

    candidates = await repository.list_human_active_candidates(
        session, now=now - timedelta(seconds=CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS),
        limit=limit,
    )
    if not candidates:
        return ReapResult()

    scanned = contended = recovered = 0
    # presence 按租户批量判定；Redis 不可用（None）= fail-closed，本租户候选全部跳过
    by_tenant: dict[str, list[dict]] = {}
    for cand in candidates:
        by_tenant.setdefault(cand["tenant_id"], []).append(cand)

    for tenant_id, tenant_candidates in by_tenant.items():
        agent_ids = sorted({c["assigned_agent_id"] for c in tenant_candidates})
        online = await presence.online_agent_ids(tenant_id=tenant_id, agent_ids=agent_ids)
        if online is None:
            logger.warning(
                "[cs-reaper] presence unavailable, skip human_active recovery (tenant=%s)",
                tenant_id,
            )
            contended += len(tenant_candidates)
            continue

        for cand in tenant_candidates:
            scanned += 1
            if cand["assigned_agent_id"] in online:
                continue  # 坐席其实在线（候选时间粗筛误伤）

            # 加锁顺序 conversations → handoffs（与全模块约定一致）
            conversation = await repository.lock_conversation(
                session, tenant_id=tenant_id,
                conversation_id=cand["conversation_id"],
            )
            if conversation is None:
                contended += 1
                continue
            handoff = await repository.lock_stale_human_active_handoff(
                session,
                tenant_id=tenant_id,
                handoff_id=cand["handoff_id"],
                assigned_agent_id=cand["assigned_agent_id"],
                assignment_version=cand["assignment_version"],
            )
            if handoff is None:
                contended += 1
                continue

            # 锁内 grace 复核：候选预读后坐席又发了消息 → 放弃本轮恢复
            last_agent_msg_at = await repository.last_human_agent_message_at(
                session, conversation_id=cand["conversation_id"],
            )
            reference = last_agent_msg_at or handoff.updated_at or now
            if (now - reference).total_seconds() < CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS:
                contended += 1
                continue

            await transition_handoff(
                session,
                tenant_id=tenant_id,
                handoff=handoff,
                conversation=conversation,
                target_state="waiting_human",
                clear_assigned_agent=True,
                bump_assignment_version=True,
                release_assignments="released",
                clear_offer_fields=True,
                event_type=EVENT_HUMAN_ACTIVE_RECOVERED,
                event_payload={
                    "reason": REASON_AGENT_OFFLINE,
                    "previous_agent_id": cand["assigned_agent_id"],
                    "offline_timeout_seconds": CS_HUMAN_ACTIVE_OFFLINE_TIMEOUT_SECONDS,
                    "notify_user": True,
                    # 自愈=主管重派同语义：重新计满重派窗口（不立即触发终态）
                    "deadline_extended": True,
                },
                actor_user_id="cs_reaper",
                now=now,
            )
            # 自愈后重新计满总等待期（与 reassign_handoff 同语义），否则
            # 人工已处理很久的工单回到队列下一 tick 就被 overdue 关闭。
            handoff.total_deadline_at = now + timedelta(
                seconds=_handoff_timeout_seconds()
            )
            recovered += 1

    return ReapResult(scanned=scanned, recovered=recovered, contended=contended)


def _handoff_timeout_seconds() -> int:
    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS

    return int(CS_HANDOFF_TIMEOUT_SECONDS)


async def reap_once(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> ReapResult:
    """一轮回收：先处理过期 offer，再关超期排队工单，最后自愈离线人工。

    顺序不可颠倒：``agent_offered`` 的行若已超总期限，需要先被
    ``reap_expired_offers`` 以 ``max_attempts``/``total_deadline`` 关闭；
    反过来先跑 ``reap_overdue_waiting`` 会漏掉这些行（它们状态不是
    ``waiting_human``）。human_active 自愈放最后：它消费 presence 且
    会让工单重新入队，前两段的终态判定先执行避免同轮重复处理。
    """
    now = now or _now()
    batch = limit if limit is not None else CS_REAPER_BATCH_LIMIT

    expired = await reap_expired_offers(session, now=now, limit=batch)
    overdue = await reap_overdue_waiting(session, now=now, limit=batch)
    recovered = await reap_stale_human_active(session, now=now, limit=batch)
    return ReapResult(
        scanned=expired.scanned + overdue.scanned + recovered.scanned,
        released=expired.released,
        closed=expired.closed + overdue.closed,
        contended=expired.contended + overdue.contended + recovered.contended,
        recovered=recovered.recovered,
    )
