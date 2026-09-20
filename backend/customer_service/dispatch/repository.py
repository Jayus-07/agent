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

from datetime import datetime
from typing import Sequence

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.handoff import CSHandoff

_WAITING_STATE = "waiting_human"
_ACTIVE_ASSIGNMENT_STATES = ("offered", "accepted")


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


def least_loaded_agent_stmt(*, tenant_id: str, online_agent_ids: Sequence[str]) -> Select:
    """最少负载 + 轮询选一名在线坐席，并锁定该行。

    排序固定为：活动数升序 → ``last_assigned_at`` NULLS FIRST 升序 →
    ``agent_id`` 升序。容量谓词 ``active < max_conversations`` 保证不超载。
    """
    active_count = active_assignment_count_expr()
    return (
        select(CSAgent)
        .where(
            CSAgent.tenant_id == tenant_id,
            CSAgent.enabled.is_(True),
            CSAgent.available.is_(True),
            CSAgent.accepting.is_(True),
            CSAgent.agent_id.in_(list(online_agent_ids)),
            active_count < CSAgent.max_conversations,
        )
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
    session: AsyncSession, *, tenant_id: str, online_agent_ids: Sequence[str]
) -> CSAgent | None:
    if not online_agent_ids:
        return None
    result = await session.execute(
        least_loaded_agent_stmt(
            tenant_id=tenant_id, online_agent_ids=online_agent_ids
        )
    )
    return result.scalars().first()


async def waiting_tenant_heads(session: AsyncSession, *, limit: int) -> list[str]:
    result = await session.execute(waiting_tenant_heads_stmt(limit=limit))
    return [str(row[0]) for row in result.all()]
