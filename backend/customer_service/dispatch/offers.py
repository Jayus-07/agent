"""dispatch/offers.py — 坐席侧 offer 生命周期（P7）。

状态机（方案 §五）：

    waiting_human ──dispatcher 原子分配──▶ agent_offered
    agent_offered ──客服接单──▶ human_active ──结束──▶ closed
    agent_offered ──拒绝 / 30 秒超时──▶ waiting_human
    agent_offered ──超过 5 次或 600 秒──▶ closed（通知用户并恢复 AI）
                                        （终态由 reaper.py 执行）

本模块负责**坐席主动**的三条转换（accept / decline）与**主管**的重派
（reassign）；被动超时由 ``reaper.py`` 负责。三者共用同一套
「工单归属 + 版本号 + 状态」判定，因此「旧版本 accept」在任何路径下都返回
409 stale，绝不会以旧版本覆盖新一轮 offer。

**加锁顺序**（与 ``repository`` 模块约定一致，禁止反向）：

``conversations`` → ``handoffs`` → ``cs_agents``

accept/decline/reassign 只从 ``handoff_id`` 入手，所以先做一次**无锁预读**
拿 conversation_id 来定序（与 P6 dispatcher 完全相同的理由：P4 入池事务同序）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.cs_dispatch import CS_OFFER_TIMEOUT_SECONDS
from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.customer_service.dispatch import outbox, repository
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.handoff import CSHandoff

OFFERED_STATE = "agent_offered"
ACTIVE_STATE = "human_active"
WAITING_STATE = "waiting_human"

EVENT_OFFER_DECLINED = "conversation.offer_declined"
EVENT_CLAIMED = "conversation.claimed"
EVENT_REASSIGNED = "conversation.reassigned"
EVENT_OFFERED = "conversation.offered"

# 可被主管重派解除的状态：已派出但未接单、或已在人工处理中。
_REASSIGNABLE_STATES = (OFFERED_STATE, ACTIVE_STATE)


class OfferNotFound(LookupError):  # noqa: N818 - API contract name
    """工单不存在（含跨租户：不区分，避免探测存在性）。"""


class OfferForbidden(PermissionError):  # noqa: N818 - API contract name
    """工单未分配给当前坐席。"""


class OfferStale(RuntimeError):  # noqa: N818 - API contract name
    """状态或版本已流转（旧 offer、已回收、已关闭）。"""


class ReassignForbidden(PermissionError):  # noqa: N818 - API contract name
    """无重派权限（需要 supervisor 或平台 admin）。"""


class ReassignConflict(RuntimeError):  # noqa: N818 - API contract name
    """目标坐席不可用或工单当前状态不允许重派。"""


@dataclass(frozen=True)
class OfferItem:
    """「我的 offer」列表项。"""

    handoff_id: str
    conversation_id: str
    user_id: str
    handoff_state: str
    assignment_version: int
    attempt_count: int
    priority: int
    offered_at: datetime | None
    offer_expires_at: datetime | None


@dataclass(frozen=True)
class OfferActionResult:
    """accept/decline/reassign 的稳定响应。"""

    handoff_id: str
    conversation_id: str
    handoff_state: str
    agent_id: str | None
    assignment_version: int
    offer_expires_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item(row: CSHandoff) -> OfferItem:
    return OfferItem(
        handoff_id=row.handoff_id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        handoff_state=row.handoff_state,
        assignment_version=int(row.assignment_version or 0),
        attempt_count=int(row.attempt_count or 0),
        priority=int(row.priority or 0),
        offered_at=row.offered_at,
        offer_expires_at=row.offer_expires_at,
    )


def _result(
    row: CSHandoff, *, agent_id: str | None, offer_expires_at: datetime | None = None
) -> OfferActionResult:
    return OfferActionResult(
        handoff_id=row.handoff_id,
        conversation_id=row.conversation_id,
        handoff_state=row.handoff_state,
        agent_id=agent_id,
        assignment_version=int(row.assignment_version or 0),
        offer_expires_at=offer_expires_at,
    )


async def _load_locked_pair(
    session: AsyncSession, *, tenant_id: str, handoff_id: str
) -> tuple[CSConversation | None, CSHandoff | None]:
    """按 ``conversations → handoffs`` 顺序锁定一对行。

    返回 ``(None, None)`` 表示工单不存在；``(None, handoff)`` 表示会话行
    被并发事务锁住（``SKIP LOCKED``），调用方应视为可重试冲突。
    """
    conversation_id = await repository.find_handoff_conversation_id(
        session, tenant_id=tenant_id, handoff_id=handoff_id
    )
    if conversation_id is None:
        return None, None

    conversation = await repository.lock_conversation(
        session, tenant_id=tenant_id, conversation_id=conversation_id
    )
    if conversation is None:
        return None, None

    handoff = await repository.lock_handoff_by_id(
        session, tenant_id=tenant_id, handoff_id=handoff_id
    )
    return conversation, handoff


def _assert_offer_owner(handoff: CSHandoff, agent_id: str) -> None:
    if (handoff.assigned_agent_id or "") != agent_id:
        raise OfferForbidden("该工单未分配给当前坐席")


def _assert_offer_fresh(
    handoff: CSHandoff,
    *,
    offer_version: int | None,
    now: datetime,
) -> None:
    if handoff.handoff_state != OFFERED_STATE:
        raise OfferStale(
            f"offer 状态已流转（当前 {handoff.handoff_state}）"
        )
    if offer_version is not None and int(offer_version) != int(
        handoff.assignment_version or 0
    ):
        raise OfferStale("offer 版本已过期，请以最新 offer 重试")
    if handoff.offer_expires_at is not None and handoff.offer_expires_at <= now:
        raise OfferStale("offer 已超时，工单已回到等待队列")


async def list_my_offers(
    session: AsyncSession, *, tenant_id: str, agent_id: str
) -> list[OfferItem]:
    """本人待接单 + 处理中工单（WS 重连后的补拉源）。"""
    rows = await repository.list_my_handoffs(
        session, tenant_id=tenant_id, agent_id=agent_id
    )
    return [_item(row) for row in rows]


async def accept_offer(
    session: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    handoff_id: str,
    offer_version: int | None = None,
    now: datetime | None = None,
) -> OfferActionResult:
    """接单：``agent_offered → human_active``。

    旧版本（``offer_version`` 与工单当前版本不一致）返回 ``OfferStale``
    —— 这是「重派后再点旧的接单按钮」的唯一正确语义，不能当作幂等成功。
    """
    now = now or _now()
    async with session.begin():
        conversation, handoff = await _load_locked_pair(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        )
        if handoff is None:
            raise OfferNotFound("handoff not found")

        _assert_offer_owner(handoff, agent_id)

        if handoff.handoff_state == ACTIVE_STATE:
            # 重复点接单：状态已流转 → 与其他 stale 路径一样 409
            #（消息区分开，方便前端提示「工单已由本坐席接单」）。
            raise OfferStale("工单已由本坐席接单")

        _assert_offer_fresh(handoff, offer_version=offer_version, now=now)

        assignments = await repository.lock_active_assignments_for_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        )
        target = next((a for a in assignments if a.agent_id == agent_id), None)
        if target is None:
            # 活动 assignment 已被 reaper/主管解除，但工单状态还没刷新：
            # 以 assignment 为准拒绝，避免"接单成功但无人负责"。
            raise OfferStale("offer 已被回收，请等待重新派单")

        handoff.handoff_state = ACTIVE_STATE
        handoff.updated_at = now
        handoff.offer_expires_at = None
        handoff.offered_at = None

        target.state = "accepted"
        target.accepted_at = now

        if conversation is not None:
            conversation.assigned_agent_id = agent_id
            conversation.handling_mode = "human"
            conversation.updated_at = now

        outbox.append_event(
            session,
            tenant_id=tenant_id,
            conversation_id=handoff.conversation_id,
            type=EVENT_CLAIMED,
            payload={
                "conversation_id": handoff.conversation_id,
                "handoff_id": handoff.handoff_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "assignment_version": int(handoff.assignment_version or 0),
            },
            handoff_id=handoff.handoff_id,
            actor_user_id=agent_id,
            now=now,
        )
        await session.flush()
        return _result(handoff, agent_id=agent_id)


async def decline_offer(
    session: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    handoff_id: str,
    offer_version: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> OfferActionResult:
    """拒单：``agent_offered → waiting_human``，并让该坐席在本工单上冷却。

    ``attempt_count`` **不回滚**：拒单消耗一次自动派单机会，否则「拒单-重派」
    可以无限循环（方案 A5 的 5 次上限会形同虚设）。
    """
    now = now or _now()
    async with session.begin():
        conversation, handoff = await _load_locked_pair(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        )
        if handoff is None:
            raise OfferNotFound("handoff not found")

        _assert_offer_owner(handoff, agent_id)
        _assert_offer_fresh(handoff, offer_version=offer_version, now=now)

        assignments = await repository.lock_active_assignments_for_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        )
        target = next((a for a in assignments if a.agent_id == agent_id), None)
        if target is None:
            raise OfferStale("offer 已被回收，请等待重新派单")

        handoff.handoff_state = WAITING_STATE
        handoff.assigned_agent_id = None
        handoff.offered_at = None
        handoff.offer_expires_at = None
        handoff.updated_at = now

        target.state = "declined"
        target.declined_at = now
        target.unassigned_at = now

        if conversation is not None:
            conversation.assigned_agent_id = None
            conversation.handling_mode = WAITING_STATE
            conversation.updated_at = now

        outbox.append_event(
            session,
            tenant_id=tenant_id,
            conversation_id=handoff.conversation_id,
            type=EVENT_OFFER_DECLINED,
            payload={
                "conversation_id": handoff.conversation_id,
                "handoff_id": handoff.handoff_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "reason": (reason or "").strip() or None,
                "assignment_version": int(handoff.assignment_version or 0),
            },
            handoff_id=handoff.handoff_id,
            actor_user_id=agent_id,
            now=now,
        )
        await session.flush()
        return _result(handoff, agent_id=None)


async def reassign_handoff(
    session: AsyncSession,
    *,
    tenant_id: str,
    supervisor_agent_id: str,
    handoff_id: str,
    target_agent_id: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> OfferActionResult:
    """主管重派：解除当前分配，并（可选）直接改派给指定坐席。

    语义：

    - ``target_agent_id`` 为空 → 工单回到 ``waiting_human`` 由 dispatcher
      重新挑选（``conversation.reassigned`` 广播）；
    - 指定坐席 → 直接落成一次新的 ``agent_offered``，复用 P6 的
      ``conversation.offered`` 契约推给该坐席。

    人工介入会把**自动重试预算重新计满**（``attempt_count=0``、
    ``total_deadline_at`` 顺延一个 600 秒窗口），否则连续几次人工重派就会
    立刻触发 5 次上限把工单关掉 —— 那是自动重试的兜底，不是主管的意图。
    ``assignment_version`` 仍然 ``+1``，保证在途的旧 accept 立刻失效。
    """
    now = now or _now()
    target = (target_agent_id or "").strip() or None

    async with session.begin():
        conversation, handoff = await _load_locked_pair(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        )
        if handoff is None:
            raise OfferNotFound("handoff not found")
        if handoff.handoff_state not in _REASSIGNABLE_STATES:
            raise ReassignConflict(
                f"工单状态 {handoff.handoff_state} 不允许重派"
            )

        previous_agent_id = handoff.assigned_agent_id

        # 目标坐席校验：同租户、启用、可接单（最后取锁，保持锁序约定）。
        target_agent = None
        if target is not None:
            target_agent = await repository.lock_least_loaded_agent(
                session,
                tenant_id=tenant_id,
                online_agent_ids=[target],
                now=now,
            )
            if target_agent is None:
                raise ReassignConflict("目标坐席不存在、未启用或不可接单")

        for assignment in await repository.lock_active_assignments_for_handoff(
            session, tenant_id=tenant_id, handoff_id=handoff_id
        ):
            assignment.state = "released"
            assignment.unassigned_at = now

        version = int(handoff.assignment_version or 0) + 1
        handoff.assignment_version = version
        handoff.attempt_count = 0
        handoff.total_deadline_at = now + timedelta(
            seconds=CS_HANDOFF_TIMEOUT_SECONDS
        )
        handoff.updated_at = now

        offer_expires_at: datetime | None = None
        if target is not None and target_agent is not None:
            offer_expires_at = now + timedelta(seconds=CS_OFFER_TIMEOUT_SECONDS)
            handoff.handoff_state = OFFERED_STATE
            handoff.assigned_agent_id = target
            handoff.offered_at = now
            handoff.offer_expires_at = offer_expires_at
            target_agent.last_assigned_at = now

            from backend.customer_service.models.assignment import CSAssignment

            session.add(
                CSAssignment(
                    tenant_id=tenant_id,
                    handoff_id=handoff.handoff_id,
                    conversation_id=handoff.conversation_id,
                    agent_id=target,
                    state="offered",
                    attempt_no=1,
                    offer_version=version,
                    offered_at=now,
                    offer_expires_at=offer_expires_at,
                    assigned_by=supervisor_agent_id,
                    assigned_at=now,
                )
            )
            if conversation is not None:
                conversation.assigned_agent_id = target
                conversation.handling_mode = WAITING_STATE
                conversation.updated_at = now
        else:
            handoff.handoff_state = WAITING_STATE
            handoff.assigned_agent_id = None
            handoff.offered_at = None
            handoff.offer_expires_at = None
            if conversation is not None:
                conversation.assigned_agent_id = None
                conversation.handling_mode = WAITING_STATE
                conversation.updated_at = now

        payload = {
            "conversation_id": handoff.conversation_id,
            "handoff_id": handoff.handoff_id,
            "tenant_id": tenant_id,
            "previous_agent_id": previous_agent_id,
            "agent_id": target,
            "reassigned_by": supervisor_agent_id,
            "reason": (reason or "").strip() or None,
            "assignment_version": version,
        }
        outbox.append_event(
            session,
            tenant_id=tenant_id,
            conversation_id=handoff.conversation_id,
            type=EVENT_REASSIGNED,
            payload=payload,
            handoff_id=handoff.handoff_id,
            actor_user_id=supervisor_agent_id,
            now=now,
        )
        if target is not None:
            # 复用 P6 的定向 offer 契约（定向 = 只有目标坐席收到）
            outbox.append_event(
                session,
                tenant_id=tenant_id,
                conversation_id=handoff.conversation_id,
                type=EVENT_OFFERED,
                payload={
                    **payload,
                    "attempt_count": 1,
                    "priority": int(handoff.priority or 0),
                    "offer_expires_at": (
                        offer_expires_at.isoformat()
                        if offer_expires_at is not None
                        else None
                    ),
                },
                handoff_id=handoff.handoff_id,
                target_agent_id=target,
                actor_user_id=supervisor_agent_id,
                now=now,
            )
        await session.flush()
        return _result(handoff, agent_id=target, offer_expires_at=offer_expires_at)
