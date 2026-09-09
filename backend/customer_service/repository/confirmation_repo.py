"""ConfirmationRepository — async CRUD for customer_service.confirmations"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.confirmation import CSConfirmation


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
