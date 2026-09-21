"""dispatch/repository.py — 派单事务的 PostgreSQL 读写原语。

所有方法强制携带 ``tenant_id``：跨租户条件不能由路由或 worker 自行拼接
（方案 §八「多租户漏条件」控制措施）。

**加锁顺序（全局约定，禁止反向获取）**

``conversations`` → ``handoffs`` → ``cs_agents``

理由：P4 用户入池事务先锁会话行、再锁活动工单行（``dispatch/service.py``
的 ``_create_in_transaction``）。派单事务若反过来先锁工单再更新会话投影，
两条路径会在同一会话上互相等待形成死锁。因此派单用一次**无锁预读**确定
候选会话，再按上述顺序取锁；预读不构成派单依据，真正的绑定权归
``lock_dispatchable_handoff`` 的 ``FOR UPDATE SKIP LOCKED``。

全部锁都用 ``SKIP LOCKED``：竞争者立即让开并返回 contended，由 worker
下一轮重试，而不是排队等待（队列头被占时同一租户会被反复争抢）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.cs_dispatch import CS_AGENT_OFFER_COOLDOWN_SECONDS
from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff

_WAITING_STATE = "waiting_human"
_OFFERED_STATE = "agent_offered"
_ACTIVE_ASSIGNMENT_STATES = ("offered", "accepted")
# 已终结、且需要让当事坐席在该工单上冷却的 assignment 状态（P7）：
# expired=超时被 reaper 回收，declined=坐席主动拒绝，released=主管重派解除。
_COOLDOWN_ASSIGNMENT_STATES = ("expired", "declined", "released")


def _dispatchable(now: datetime):
    """可派工单谓词：仍排队、且未超过总等待截止时间。

    超过 ``total_deadline_at`` 的工单由 reaper（P7）关闭并通知用户，
    dispatcher 只跳过，不在这里改状态（方案 Q5）。
    """
    return and_(
        CSHandoff.handoff_state == _WAITING_STATE,
        or_(CSHandoff.total_deadline_at.is_(None), CSHandoff.total_deadline_at > now),
    )


def next_waiting_handoff_stmt(*, tenant_id: str, now: datetime) -> Select:
    """队列头（无锁预读）：严格 ``priority DESC, created_at ASC, id ASC``。"""
    return (
        select(CSHandoff)
        .where(CSHandoff.tenant_id == tenant_id, _dispatchable(now))
        .order_by(
            CSHandoff.priority.desc(),
            CSHandoff.created_at.asc(),
            CSHandoff.id.asc(),
        )
        .limit(1)
    )


def lock_waiting_handoff_stmt(
    *, tenant_id: str, conversation_id: str, now: datetime
) -> Select:
    """按会话锁定队列工单（真正的绑定资格）。"""
    return (
        select(CSHandoff)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.conversation_id == conversation_id,
            _dispatchable(now),
        )
        .order_by(CSHandoff.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def lock_conversation_stmt(*, tenant_id: str, conversation_id: str) -> Select:
    return (
        select(CSConversation)
        .where(
            CSConversation.tenant_id == tenant_id,
            CSConversation.conversation_id == conversation_id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def handoff_conversation_id_stmt(*, tenant_id: str, handoff_id: str) -> Select:
    """无锁预读：按 ``handoff_id`` 拿 conversation_id（仅用于确定加锁顺序）。"""
    return (
        select(CSHandoff.conversation_id)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.handoff_id == handoff_id,
        )
        .limit(1)
    )


def lock_handoff_by_id_stmt(*, tenant_id: str, handoff_id: str) -> Select:
    """按 ``handoff_id`` 锁定工单行（坐席侧 accept/decline/reassign 用）。

    不加状态过滤：调用方需要区分「不存在」与「状态已流转」，状态判定
    由服务层做，才能给出 404 / 403 / 409 的精确语义。
    """
    return (
        select(CSHandoff)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.handoff_id == handoff_id,
        )
        .with_for_update()
        .limit(1)
    )


def my_handoffs_stmt(
    *, tenant_id: str, agent_id: str, include_closed: bool = False
) -> Select:
    """分配给该坐席的工单（「我的 offer」补拉源）。

    覆盖 ``agent_offered``（待接单）与 ``human_active``（处理中）；未关闭的
    历史分配用 ``assigned_agent_id`` 判定即可，不需要 join assignment。
    """
    conditions = [
        CSHandoff.tenant_id == tenant_id,
        CSHandoff.assigned_agent_id == agent_id,
    ]
    if not include_closed:
        conditions.append(CSHandoff.handoff_state != "closed")
    return (
        select(CSHandoff)
        .where(*conditions)
        .order_by(
            CSHandoff.offered_at.desc().nulls_last(),
            CSHandoff.updated_at.desc(),
            CSHandoff.id.desc(),
        )
    )


def enabled_agent_id_stmt(*, tenant_id: str, auth_user_id: str) -> Select:
    """按登录用户反查启用坐席（``cs_agents.auth_user_id`` 唯一索引）。"""
    return (
        select(CSAgent.agent_id)
        .where(
            CSAgent.tenant_id == tenant_id,
            CSAgent.auth_user_id == auth_user_id,
            CSAgent.enabled.is_(True),
        )
        .limit(1)
    )


def agent_role_stmt(*, tenant_id: str, agent_id: str) -> Select:
    """坐席角色（``agent`` | ``supervisor``），用于重派权限判定。"""
    return (
        select(CSAgent.role)
        .where(
            CSAgent.tenant_id == tenant_id,
            CSAgent.agent_id == agent_id,
        )
        .limit(1)
    )


def active_assignment_count_expr():
    """同一租户同一坐席的活动 assignment 数（offered|accepted）。"""
    return (
        select(func.count())
        .select_from(CSAssignment)
        .where(
            CSAssignment.tenant_id == CSAgent.tenant_id,
            CSAssignment.agent_id == CSAgent.agent_id,
            CSAssignment.state.in_(_ACTIVE_ASSIGNMENT_STATES),
        )
        .correlate(CSAgent)
        .scalar_subquery()
    )


def agent_in_offer_cooldown_expr(*, handoff_id: str, now: datetime):
    """该坐席是否在**本工单**上处于 offer 冷却期（P7「排除刚超时客服」）。

    判定：存在一条本租户本坐席本工单的终结 assignment（expired/declined/
    released）且 ``unassigned_at`` 晚于 ``now - CS_AGENT_OFFER_COOLDOWN_SECONDS``。

    不加这个谓词时，「唯一在线坐席反复不接单」会让同一对人选被无限重试，
    直到 600 秒总等待期耗尽；加了之后冷却期内该坐席被跳过，工单继续排队
    由其他坐席接走（或到达 attempt/总期限终态）。
    """
    cooldown_before = now - timedelta(
        seconds=CS_AGENT_OFFER_COOLDOWN_SECONDS
    )
    return ~exists(
        select(CSAssignment.id)
        .where(
            CSAssignment.tenant_id == CSAgent.tenant_id,
            CSAssignment.agent_id == CSAgent.agent_id,
            CSAssignment.handoff_id == handoff_id,
            CSAssignment.state.in_(_COOLDOWN_ASSIGNMENT_STATES),
            CSAssignment.unassigned_at.is_not(None),
            CSAssignment.unassigned_at > cooldown_before,
        )
        .correlate(CSAgent)
    )


def least_loaded_agent_stmt(
    *,
    tenant_id: str,
    online_agent_ids: Sequence[str],
    handoff_id: str | None = None,
    now: datetime | None = None,
) -> Select:
    """最少负载 + 轮询选一名在线坐席，并锁定该行。

    排序固定为：活动数升序 → ``last_assigned_at`` NULLS FIRST 升序 →
    ``agent_id`` 升序。容量谓词 ``active < max_conversations`` 保证不超载。

    传入 ``handoff_id`` + ``now`` 时额外排除处于 offer 冷却期的坐席
    （见 ``agent_in_offer_cooldown_expr``）；缺省不排除，保持 P6 行为。
    """
    active_count = active_assignment_count_expr()
    conditions = [
        CSAgent.tenant_id == tenant_id,
        CSAgent.enabled.is_(True),
        CSAgent.available.is_(True),
        CSAgent.accepting.is_(True),
        CSAgent.agent_id.in_(list(online_agent_ids)),
        active_count < CSAgent.max_conversations,
    ]
    if handoff_id and now is not None:
        conditions.append(
            agent_in_offer_cooldown_expr(handoff_id=handoff_id, now=now)
        )
    return (
        select(CSAgent)
        .where(*conditions)
        .order_by(
            active_count.asc(),
            CSAgent.last_assigned_at.asc().nulls_first(),
            CSAgent.agent_id.asc(),
        )
        .with_for_update(of=CSAgent, skip_locked=True)
        .limit(1)
    )


def waiting_tenant_heads_stmt(*, limit: int) -> Select:
    """按**全局队列顺序**返回持有队头工单的租户。

    dispatcher 每次只允许派一单，但需要知道先看哪个租户才能维持
    "存在更高优先级可派工单时不得先派低优先级" 的跨租户语义。
    """
    head = (
        select(
            CSHandoff.tenant_id,
            CSHandoff.priority,
            CSHandoff.created_at,
            CSHandoff.id,
        )
        .where(CSHandoff.handoff_state == _WAITING_STATE)
        .distinct(CSHandoff.tenant_id)
        .order_by(
            CSHandoff.tenant_id,
            CSHandoff.priority.desc(),
            CSHandoff.created_at.asc(),
            CSHandoff.id.asc(),
        )
        .subquery()
    )
    return (
        select(head.c.tenant_id)
        .order_by(head.c.priority.desc(), head.c.created_at.asc(), head.c.id.asc())
        .limit(limit)
    )


# ── 执行器 ─────────────────────────────────────────────────


async def find_dispatchable_handoff_candidate(
    session: AsyncSession, *, tenant_id: str, now: datetime
) -> CSHandoff | None:
    """无锁预读队头；仅用于确定加锁顺序，不作为派单依据。"""
    result = await session.execute(next_waiting_handoff_stmt(tenant_id=tenant_id, now=now))
    return result.scalars().first()


async def lock_conversation(
    session: AsyncSession, *, tenant_id: str, conversation_id: str
) -> CSConversation | None:
    result = await session.execute(
        lock_conversation_stmt(tenant_id=tenant_id, conversation_id=conversation_id)
    )
    return result.scalars().first()


async def find_handoff_conversation_id(
    session: AsyncSession, *, tenant_id: str, handoff_id: str
) -> str | None:
    result = await session.execute(
        handoff_conversation_id_stmt(tenant_id=tenant_id, handoff_id=handoff_id)
    )
    return result.scalar_one_or_none()


async def lock_handoff_by_id(
    session: AsyncSession, *, tenant_id: str, handoff_id: str
) -> CSHandoff | None:
    result = await session.execute(
        lock_handoff_by_id_stmt(tenant_id=tenant_id, handoff_id=handoff_id)
    )
    return result.scalars().first()


async def list_my_handoffs(
    session: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    include_closed: bool = False,
) -> list[CSHandoff]:
    result = await session.execute(
        my_handoffs_stmt(
            tenant_id=tenant_id, agent_id=agent_id, include_closed=include_closed
        )
    )
    return list(result.scalars().all())


async def find_enabled_agent_id(
    session: AsyncSession, *, tenant_id: str, auth_user_id: str
) -> str | None:
    result = await session.execute(
        enabled_agent_id_stmt(tenant_id=tenant_id, auth_user_id=auth_user_id)
    )
    return result.scalar_one_or_none()


async def find_agent_role(
    session: AsyncSession, *, tenant_id: str, agent_id: str
) -> str | None:
    result = await session.execute(
        agent_role_stmt(tenant_id=tenant_id, agent_id=agent_id)
    )
    return result.scalar_one_or_none()


async def lock_dispatchable_handoff(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation_id: str,
    now: datetime,
) -> CSHandoff | None:
    result = await session.execute(
        lock_waiting_handoff_stmt(
            tenant_id=tenant_id, conversation_id=conversation_id, now=now
        )
    )
    return result.scalars().first()


async def list_accepting_agent_ids(
    session: AsyncSession, *, tenant_id: str
) -> list[str]:
    """本租户「启用 + 可接单 + 可分配」的坐席 ID（在线性由 Redis 判定）。"""
    result = await session.execute(
        select(CSAgent.agent_id)
        .where(
            CSAgent.tenant_id == tenant_id,
            CSAgent.enabled.is_(True),
            CSAgent.available.is_(True),
            CSAgent.accepting.is_(True),
        )
        .order_by(CSAgent.agent_id.asc())
    )
    return list(result.scalars().all())


async def lock_least_loaded_agent(
    session: AsyncSession,
    *,
    tenant_id: str,
    online_agent_ids: Sequence[str],
    handoff_id: str | None = None,
    now: datetime | None = None,
) -> CSAgent | None:
    if not online_agent_ids:
        return None
    result = await session.execute(
        least_loaded_agent_stmt(
            tenant_id=tenant_id,
            online_agent_ids=online_agent_ids,
            handoff_id=handoff_id,
            now=now,
        )
    )
    return result.scalars().first()


async def waiting_tenant_heads(session: AsyncSession, *, limit: int) -> list[str]:
    result = await session.execute(waiting_tenant_heads_stmt(limit=limit))
    return [str(row[0]) for row in result.all()]


# ── P7 reaper：回收过期 offer 与超总等待期的工单 ────────────────
#
# 为什么是「无锁候选 + 逐行按序取锁」而不是一条 FOR UPDATE SKIP LOCKED 批量
# 查询：reaper 需要同时改 handoff 与其 conversation 投影，若先锁 handoff
# 再锁 conversation，就与 P4 入池 / P7 接单的 ``conversations → handoffs``
# 顺序相反，同一行集上会死锁。因此候选先无锁读出（只为了拿到 conversation_id
# 定序），再按约定顺序取锁并在锁内复核状态；被别的事务持有则 SKIP LOCKED
# 跳过，下一轮（1 秒后）自然重试。


def expired_offer_candidates_stmt(*, now: datetime, limit: int) -> Select:
    """仍 ``agent_offered`` 但 ``offer_expires_at`` 已过的工单（无锁候选）。"""
    return (
        select(
            CSHandoff.tenant_id,
            CSHandoff.handoff_id,
            CSHandoff.conversation_id,
        )
        .where(
            CSHandoff.handoff_state == _OFFERED_STATE,
            CSHandoff.offer_expires_at.is_not(None),
            CSHandoff.offer_expires_at <= now,
        )
        .order_by(CSHandoff.offer_expires_at.asc(), CSHandoff.id.asc())
        .limit(limit)
    )


def overdue_waiting_candidates_stmt(*, now: datetime, limit: int) -> Select:
    """仍在排队但已超总等待期的工单（含 P6 刻意跳过的 Q5 终态，无锁候选）。"""
    return (
        select(
            CSHandoff.tenant_id,
            CSHandoff.handoff_id,
            CSHandoff.conversation_id,
        )
        .where(
            CSHandoff.handoff_state == _WAITING_STATE,
            CSHandoff.total_deadline_at.is_not(None),
            CSHandoff.total_deadline_at <= now,
        )
        .order_by(CSHandoff.total_deadline_at.asc(), CSHandoff.id.asc())
        .limit(limit)
    )


def lock_expired_offer_handoff_stmt(
    *, tenant_id: str, handoff_id: str, now: datetime
) -> Select:
    """锁内复核：仍是过期 offer 才交给 reaper 回收。"""
    return (
        select(CSHandoff)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.handoff_id == handoff_id,
            CSHandoff.handoff_state == _OFFERED_STATE,
            CSHandoff.offer_expires_at.is_not(None),
            CSHandoff.offer_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def lock_overdue_waiting_handoff_stmt(
    *, tenant_id: str, handoff_id: str, now: datetime
) -> Select:
    return (
        select(CSHandoff)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.handoff_id == handoff_id,
            CSHandoff.handoff_state == _WAITING_STATE,
            CSHandoff.total_deadline_at.is_not(None),
            CSHandoff.total_deadline_at <= now,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def active_assignments_for_handoff_stmt(*, tenant_id: str, handoff_id: str) -> Select:
    """锁定该工单当前的活动 assignment（offered|accepted）。"""
    return (
        select(CSAssignment)
        .where(
            CSAssignment.tenant_id == tenant_id,
            CSAssignment.handoff_id == handoff_id,
            CSAssignment.state.in_(_ACTIVE_ASSIGNMENT_STATES),
        )
        .order_by(CSAssignment.id.asc())
        .with_for_update()
    )


async def list_expired_offer_candidates(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[tuple[str, str, str]]:
    result = await session.execute(
        expired_offer_candidates_stmt(now=now, limit=limit)
    )
    return [(str(row[0]), str(row[1]), str(row[2])) for row in result.all()]


async def list_overdue_waiting_candidates(
    session: AsyncSession, *, now: datetime, limit: int
) -> list[tuple[str, str, str]]:
    result = await session.execute(
        overdue_waiting_candidates_stmt(now=now, limit=limit)
    )
    return [(str(row[0]), str(row[1]), str(row[2])) for row in result.all()]


async def lock_expired_offer_handoff(
    session: AsyncSession, *, tenant_id: str, handoff_id: str, now: datetime
) -> CSHandoff | None:
    result = await session.execute(
        lock_expired_offer_handoff_stmt(
            tenant_id=tenant_id, handoff_id=handoff_id, now=now
        )
    )
    return result.scalars().first()


async def lock_overdue_waiting_handoff(
    session: AsyncSession, *, tenant_id: str, handoff_id: str, now: datetime
) -> CSHandoff | None:
    result = await session.execute(
        lock_overdue_waiting_handoff_stmt(
            tenant_id=tenant_id, handoff_id=handoff_id, now=now
        )
    )
    return result.scalars().first()


async def lock_active_assignments_for_handoff(
    session: AsyncSession, *, tenant_id: str, handoff_id: str
) -> list[CSAssignment]:
    result = await session.execute(
        active_assignments_for_handoff_stmt(
            tenant_id=tenant_id, handoff_id=handoff_id
        )
    )
    return list(result.scalars().all())


# ── P8 outbox relay：持久事件投递 ─────────────────────────────


def pending_outbox_events_stmt(*, limit: int) -> Select:
    """待投递事件：按 ``id`` 升序（同会话事件顺序 = seq 顺序）。

    ``FOR UPDATE SKIP LOCKED`` 让多副本 relay 天然分片：被别处锁定的行
    本轮跳过，不排队、不重复发布；发布成功后才标 ``published``。
    """
    return (
        select(CSEvent)
        .where(CSEvent.outbox_status == "pending")
        .order_by(CSEvent.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    )


def oldest_pending_outbox_stmt() -> Select:
    """最老一条待投递事件（算 outbox lag = now - created_at）。"""
    return (
        select(CSEvent.created_at)
        .where(CSEvent.outbox_status == "pending")
        .order_by(CSEvent.id.asc())
        .limit(1)
    )


def pending_outbox_count_stmt():
    return (
        select(func.count())
        .select_from(CSEvent)
        .where(CSEvent.outbox_status == "pending")
    )


async def lock_pending_outbox_events(
    session: AsyncSession, *, limit: int
) -> list[CSEvent]:
    result = await session.execute(pending_outbox_events_stmt(limit=limit))
    return list(result.scalars().all())


async def oldest_pending_outbox_created_at(
    session: AsyncSession,
) -> datetime | None:
    return (await session.execute(oldest_pending_outbox_stmt())).scalar_one_or_none()


async def count_pending_outbox(session: AsyncSession) -> int:
    return int((await session.execute(pending_outbox_count_stmt())).scalar_one())


# ── P8 运营统计（队列深度 / 在线坐席）────────────────────────


def waiting_handoff_count_stmt(*, tenant_id: str | None = None) -> Select:
    stmt = (
        select(CSHandoff.tenant_id, func.count().label("waiting"))
        .where(CSHandoff.handoff_state == _WAITING_STATE)
        .group_by(CSHandoff.tenant_id)
    )
    if tenant_id is not None:
        stmt = stmt.where(CSHandoff.tenant_id == tenant_id)
    return stmt


def offering_handoff_count_stmt(*, tenant_id: str | None = None) -> Select:
    stmt = (
        select(CSHandoff.tenant_id, func.count().label("offering"))
        .where(CSHandoff.handoff_state == _OFFERED_STATE)
        .group_by(CSHandoff.tenant_id)
    )
    if tenant_id is not None:
        stmt = stmt.where(CSHandoff.tenant_id == tenant_id)
    return stmt


def enabled_agent_count_stmt(*, tenant_id: str | None = None) -> Select:
    stmt = (
        select(CSAgent.tenant_id, func.count().label("enabled_agents"))
        .where(CSAgent.enabled.is_(True))
        .group_by(CSAgent.tenant_id)
    )
    if tenant_id is not None:
        stmt = stmt.where(CSAgent.tenant_id == tenant_id)
    return stmt


async def count_queue_by_tenant(
    session: AsyncSession,
) -> dict[str, int]:
    """返回 {tenant_id: 排队中工单数}（未出现的租户视为 0）。"""
    result = await session.execute(waiting_handoff_count_stmt())
    return {str(row[0]): int(row[1]) for row in result.all()}


async def count_offering_by_tenant(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(offering_handoff_count_stmt())
    return {str(row[0]): int(row[1]) for row in result.all()}


async def count_enabled_agents_by_tenant(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(enabled_agent_count_stmt())
    return {str(row[0]): int(row[1]) for row in result.all()}
