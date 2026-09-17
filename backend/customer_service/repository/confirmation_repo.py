"""ConfirmationRepository — async CRUD for customer_service.confirmations

P1 重构（2026-09-17）：
  - claim_pending / finalize_current 提供原子条件更新（幂等基础）。
    此前 update_state 是无前置条件的裸 UPDATE，重复确认会双执行。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.confirmation import CSConfirmation

# 可被 finalize 的中间态（pending = 未认领；confirmed/executing = 已认领执行中）
_ACTIVE_CONFIRM_STATES = ("pending", "confirmed", "executing")


class ConfirmationRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def load(self, user_id: str, conversation_id: str) -> CSConfirmation | None:
        result = await self._s.execute(
            select(CSConfirmation).where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        user_id: str,
        conversation_id: str,
        pending_action: dict,
    ) -> CSConfirmation:
        confirmation_id = pending_action.get("action_id", str(uuid.uuid4()))
        obj = CSConfirmation(
            confirmation_id=confirmation_id,
            conversation_id=conversation_id,
            user_id=user_id,
            action_type=pending_action.get("action_type", ""),
            target_type=pending_action.get("target_type", ""),
            target_id=pending_action.get("target_id", ""),
            proposal=pending_action,
            state=pending_action.get("confirmation_state", "pending"),
            expires_at=_parse_dt(pending_action.get("expires_at")),
        )
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def update_state(
        self,
        confirmation_id: str,
        state: str,
        confirmed_at: datetime | None = None,
        executed_at: datetime | None = None,
    ) -> bool:
        values: dict = {"state": state}
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        if executed_at is not None:
            values["executed_at"] = executed_at
        result = await self._s.execute(
            update(CSConfirmation)
            .where(CSConfirmation.confirmation_id == confirmation_id)
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def clear(self, user_id: str, conversation_id: str) -> bool:
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state == "pending",
            )
            .values(state="cancelled")
        )
        await self._s.flush()
        return result.rowcount > 0

    async def claim_pending(
        self, user_id: str, conversation_id: str
    ) -> str | None:
        """原子认领：pending → confirmed（幂等闸门）。

        单条 UPDATE 带前置条件 state='pending'，并发重复确认只有一方
        能成功（rowcount=1），另一方拿到 None —— 阻止双执行。
        返回被认领的 confirmation_id；无 pending 行 / 已被处理返回 None。
        """
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state == "pending",
            )
            .values(
                state="confirmed",
                confirmed_at=datetime.now(timezone.utc),
            )
            .returning(CSConfirmation.confirmation_id)
        )
        await self._s.flush()
        rows = result.scalars().all()
        return rows[0] if rows else None

    async def finalize_current(
        self, user_id: str, conversation_id: str, final_state: str
    ) -> bool:
        """把该会话当前确认行置为终态（success/failed/expired），幂等。

        修正历史缺陷：过期/失败此前被 repo.clear 一律写成 cancelled，
        审计口径失真（audit-report §P1-14）。
        """
        values: dict = {"state": final_state}
        if final_state in ("success", "failed"):
            values["executed_at"] = datetime.now(timezone.utc)
        result = await self._s.execute(
            update(CSConfirmation)
            .where(
                CSConfirmation.user_id == user_id,
                CSConfirmation.conversation_id == conversation_id,
                CSConfirmation.state.in_(_ACTIVE_CONFIRM_STATES),
            )
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def has_pending(self, user_id: str) -> bool:
        result = await self._s.execute(
            select(
                exists().where(
                    CSConfirmation.user_id == user_id,
                    CSConfirmation.state == "pending",
                )
            )
        )
        return bool(result.scalar())


def _parse_dt(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
