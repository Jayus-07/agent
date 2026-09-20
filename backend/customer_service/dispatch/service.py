"""客服派单域服务。

P4（``create_or_reuse_handoff``）：把用户请求可靠地落成 ``waiting_human`` 工单。
P6（``dispatch_once``）：在一个 PostgreSQL 事务内完成优先级取单、在线/容量
过滤、最少负载轮询选择、``agent_offered`` 转换、assignment 写入、会话投影
更新与持久定向事件，提交后再广播。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.cs_dispatch import CS_OFFER_TIMEOUT_SECONDS
from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.customer_service.dispatch import event_relay, presence, repository
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff
from backend.memory.database import MemoryDatabaseUnavailable
from backend.shared.logger import logger

OFFER_EVENT_TYPE = "conversation.offered"
_ASSIGNED_BY = "cs_dispatcher"


class ConversationNotFound(LookupError):  # noqa: N818 - API contract name
    """会话不存在。"""


class ConversationForbidden(PermissionError):  # noqa: N818 - API contract name
    """会话不属于当前可信用户或租户。"""


class HandoffConflict(RuntimeError):  # noqa: N818 - API contract name
    """会话当前状态不允许再次进入人工队列。"""


@dataclass(frozen=True)
class HandoffResult:
    """用户入池 API 的稳定响应。"""

    handoff_id: str
    conversation_id: str
    handoff_state: str
    total_deadline_at: datetime | None
    reused: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_result(row: CSHandoff, *, reused: bool) -> HandoffResult:
    return HandoffResult(
        handoff_id=row.handoff_id,
        conversation_id=row.conversation_id,
        handoff_state=row.handoff_state,
        total_deadline_at=row.total_deadline_at,
        reused=reused,
    )


async def _load_active_handoff(
    session: AsyncSession,
    *,
    conversation_id: str,
    tenant_id: str,
) -> CSHandoff | None:
    result = await session.execute(
        select(CSHandoff)
        .where(
            CSHandoff.tenant_id == tenant_id,
            CSHandoff.conversation_id == conversation_id,
            CSHandoff.handoff_state != "closed",
        )
        .with_for_update()
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _create_in_transaction(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    idempotency_key: str,
) -> HandoffResult:
    conversation_result = await session.execute(
        select(CSConversation)
        .where(CSConversation.conversation_id == conversation_id)
        .with_for_update()
    )
    conversation = conversation_result.scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFound("conversation not found")

    if (
        conversation.user_id != user_id
        or conversation.tenant_id != tenant_id
    ):
        raise ConversationForbidden("conversation owner or tenant mismatch")

    active = await _load_active_handoff(
        session,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
    )
    if active is not None:
        return _as_result(active, reused=True)

    if conversation.conversation_status == "resolved":
        raise HandoffConflict("resolved conversation cannot enter handoff")
    if conversation.handling_mode == "human":
        raise HandoffConflict("conversation is already handled by an agent")

    now = _now()
    handoff = CSHandoff(
        handoff_id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        handoff_state="waiting_human",
        trigger_type="explicit_request",
        trigger_reason="用户点击转接人工",
        priority=50,
        idempotency_key=idempotency_key,
        total_deadline_at=now + timedelta(seconds=CS_HANDOFF_TIMEOUT_SECONDS),
        created_at=now,
        updated_at=now,
    )
    session.add(handoff)

    # 与 handoff 在同一事务中更新会话投影；dispatcher 只消费已提交的
    # waiting_human 行，因此不会看到半成品状态。
    conversation.handling_mode = "waiting_human"
    conversation.updated_at = now
    conversation.last_activity_at = now
    await session.flush()
    return _as_result(handoff, reused=False)


async def _reuse_after_integrity_error(
    session: AsyncSession,
    *,
    conversation_id: str,
    tenant_id: str,
) -> HandoffResult | None:
    """唯一索引竞争后重新读取赢家；没有赢家则不能假报成功。"""
    await session.rollback()
    try:
        async with session.begin():
            active = await _load_active_handoff(
                session,
                conversation_id=conversation_id,
                tenant_id=tenant_id,
            )
            return _as_result(active, reused=True) if active else None
    except SQLAlchemyError as exc:
        raise MemoryDatabaseUnavailable(
            "customer_service handoff lookup failed after uniqueness conflict"
        ) from exc


async def create_or_reuse_handoff(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    idempotency_key: str,
) -> HandoffResult:
    """在 PostgreSQL 事务中创建或复用同会话活动工单。

    会话行锁把同一会话的并发请求串行化；活动唯一索引是第二道防线。
    ``IntegrityError`` 只有在另一条历史路径同时写入时才走重新读取分支，
    重新读取不到赢家就返回数据库失败，绝不把未提交结果当作成功。
    """
    try:
        async with session.begin():
            return await _create_in_transaction(
                session,
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
            )
    except (ConversationNotFound, ConversationForbidden, HandoffConflict):
        raise
    except IntegrityError:
        recovered = await _reuse_after_integrity_error(
            session,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
        )
        if recovered is not None:
            return recovered
        raise MemoryDatabaseUnavailable(
            "customer_service handoff uniqueness conflict"
        )
    except SQLAlchemyError as exc:
        raise MemoryDatabaseUnavailable(
            "customer_service handoff database unavailable"
        ) from exc


# ── P6 自动派单 ─────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchResult:
    """一次派单尝试的结构化结果（worker 观测与并发测试共用）。"""

    status: str
    handoff_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    assignment_version: int | None = None
    offer_expires_at: datetime | None = None
    detail: str | None = None


def _iso(value: datetime) -> str:
    return value.isoformat()


async def dispatch_once(
    session: AsyncSession,
    *,
    tenant_id: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> DispatchResult:
    """尝试给该租户队列头工单选一名坐席并绑定（最多一单）。

    返回值 status 语义：

    - ``dispatched``            已绑定，事务已提交，广播已发出（不回溯失败）
    - ``shadow``                ``dry_run=True``：完成取单/在线/容量/排序，
                                但不写任何行、不广播（P9 灰度观察用）
    - ``no_handoff``            队列里没有可派工单（含全部超总等待期）
    - ``contended``             预读到的候选在取锁前被别的 worker 拿走/状态变化
    - ``presence_unavailable``  Redis 不可用 → fail-closed，本轮不派单
    - ``no_candidate``          在线坐席为空或全部满载

    事务边界：所有写操作都在同一个 ``session.begin()`` 里；广播在提交之后，
    广播异常不回滚绑定（方案 §六 P6 第 6 步）。
    """
    now = now or _now()
    tenant_id = str(tenant_id or "").strip()
    if not tenant_id:
        return DispatchResult(status="no_candidate", detail="missing trusted tenant")

    envelope: dict | None = None
    result: DispatchResult

    async with session.begin():
        candidate = await repository.find_dispatchable_handoff_candidate(
            session, tenant_id=tenant_id, now=now
        )
        if candidate is None:
            return DispatchResult(status="no_handoff")

        # 加锁顺序 conversations → handoffs → cs_agents（见 repository 模块
        # docstring）：P4 入池事务同序，反向取锁会死锁。
        conversation = await repository.lock_conversation(
            session, tenant_id=tenant_id, conversation_id=candidate.conversation_id
        )
        if conversation is None:
            return DispatchResult(
                status="contended",
                handoff_id=candidate.handoff_id,
                conversation_id=candidate.conversation_id,
            )

        handoff = await repository.lock_dispatchable_handoff(
            session,
            tenant_id=tenant_id,
            conversation_id=candidate.conversation_id,
            now=now,
        )
        if handoff is None:
            return DispatchResult(
                status="contended", conversation_id=candidate.conversation_id
            )

        candidate_ids = await repository.list_accepting_agent_ids(
            session, tenant_id=tenant_id
        )
        online = await presence.online_agent_ids(
            tenant_id=tenant_id, agent_ids=candidate_ids
        )
        if online is None:
            return DispatchResult(
                status="presence_unavailable",
                handoff_id=handoff.handoff_id,
                conversation_id=handoff.conversation_id,
            )
        if not online:
            return DispatchResult(
                status="no_candidate",
                handoff_id=handoff.handoff_id,
                conversation_id=handoff.conversation_id,
            )

        agent = await repository.lock_least_loaded_agent(
            session, tenant_id=tenant_id, online_agent_ids=sorted(online)
        )
        if agent is None:
            return DispatchResult(
                status="no_candidate",
                handoff_id=handoff.handoff_id,
                conversation_id=handoff.conversation_id,
            )

        version = int(handoff.assignment_version or 0) + 1
        attempt = int(handoff.attempt_count or 0) + 1
        offer_expires_at = now + timedelta(seconds=CS_OFFER_TIMEOUT_SECONDS)

        if dry_run:
            # shadow：候选与排序已算出，但不写行、不广播。事务内没有任何
            # 写操作，退出 context manager 即提交一个空事务并释放读锁。
            return DispatchResult(
                status="shadow",
                handoff_id=handoff.handoff_id,
                conversation_id=handoff.conversation_id,
                agent_id=agent.agent_id,
                assignment_version=version,
                offer_expires_at=offer_expires_at,
            )

        handoff.handoff_state = "agent_offered"
        handoff.assigned_agent_id = agent.agent_id
        handoff.assignment_version = version
        handoff.attempt_count = attempt
        handoff.offered_at = now
        handoff.offer_expires_at = offer_expires_at
        handoff.updated_at = now

        # 轮询公平性依赖它：下一次选择时该坐席排在后面。
        agent.last_assigned_at = now

        session.add(
            CSAssignment(
                tenant_id=tenant_id,
                handoff_id=handoff.handoff_id,
                conversation_id=handoff.conversation_id,
                agent_id=agent.agent_id,
                state="offered",
                attempt_no=attempt,
                offer_version=version,
                offered_at=now,
                offer_expires_at=offer_expires_at,
                assigned_by=_ASSIGNED_BY,
                assigned_at=now,
            )
        )

        # 会话投影：分配可见但会话仍归等待队列（handling_mode 由接单改为 human）。
        conversation.assigned_agent_id = agent.agent_id
        conversation.handling_mode = "waiting_human"
        conversation.updated_at = now

        event_id = uuid.uuid4().hex
        payload = {
            "conversation_id": handoff.conversation_id,
            "handoff_id": handoff.handoff_id,
            "tenant_id": tenant_id,
            "agent_id": agent.agent_id,
            "assignment_version": version,
            "attempt_count": attempt,
            "priority": handoff.priority,
            "offer_expires_at": _iso(offer_expires_at),
        }
        event = CSEvent(
            conversation_id=handoff.conversation_id,
            event_id=event_id,
            tenant_id=tenant_id,
            handoff_id=handoff.handoff_id,
            target_agent_id=agent.agent_id,
            event_seq=None,
            type=OFFER_EVENT_TYPE,
            payload=payload,
            outbox_status="pending",
            created_at=now,
        )
        session.add(event)
        await session.flush()

        result = DispatchResult(
            status="dispatched",
            handoff_id=handoff.handoff_id,
            conversation_id=handoff.conversation_id,
            agent_id=agent.agent_id,
            assignment_version=version,
            offer_expires_at=offer_expires_at,
        )
        envelope = {
            "type": OFFER_EVENT_TYPE,
            "event_id": event_id,
            "seq": event.id,
            "ts": _iso(now),
            "target_agent_id": agent.agent_id,
            **payload,
        }

    # 提交之后才广播；广播失败只记日志，绝不影响已提交的绑定。
    if envelope is not None:
        try:
            event_relay.publish_persisted_event(envelope)
        except Exception:
            logger.warning(
                "[cs-dispatch] offer broadcast raised unexpectedly (handoff=%s)",
                result.handoff_id,
                exc_info=True,
            )
    return result


async def dispatch_one_tenant(
    session: AsyncSession,
    *,
    tenant_id: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> DispatchResult:
    """``dispatch_once`` 的显式别名，供 worker 与测试使用。"""
    return await dispatch_once(session, tenant_id=tenant_id, now=now, dry_run=dry_run)
