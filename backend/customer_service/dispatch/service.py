"""用户转人工入池服务。

P4 只负责把用户请求可靠地落成 ``waiting_human`` 工单。
派单、Redis 在线状态和事件 relay 由后续阶段消费这里的持久状态。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.handoff import CSHandoff
from backend.memory.database import MemoryDatabaseUnavailable


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
