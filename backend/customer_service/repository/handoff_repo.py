"""HandoffRepository — async CRUD for customer_service.handoffs"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.handoff import CSHandoff


class HandoffRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def load(self, user_id: str, conversation_id: str) -> CSHandoff | None:
        result = await self._s.execute(
            select(CSHandoff).where(
                CSHandoff.user_id == user_id,
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        user_id: str,
        conversation_id: str,
        handoff_data: dict,
    ) -> CSHandoff:
        handoff_id = handoff_data.get("handoff_id", str(uuid.uuid4()))
        obj = CSHandoff(
            handoff_id=handoff_id,
            conversation_id=conversation_id,
            user_id=user_id,
            handoff_state=handoff_data.get("handoff_state", "initiated"),
            trigger_type=handoff_data.get("trigger_type"),
            trigger_reason=handoff_data.get("trigger_reason"),
            ticket_id=handoff_data.get("ticket_id"),
        )
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def update_state(
        self,
        handoff_id: str,
        state: str,
        closed_at: datetime | None = None,
    ) -> bool:
        values: dict = {"handoff_state": state}
        if closed_at is not None:
            values["closed_at"] = closed_at
        result = await self._s.execute(
            update(CSHandoff)
            .where(CSHandoff.handoff_id == handoff_id)
            .values(**values)
        )
        await self._s.flush()
        return result.rowcount > 0

    async def clear(self, user_id: str, conversation_id: str) -> bool:
        result = await self._s.execute(
            update(CSHandoff)
            .where(
                CSHandoff.user_id == user_id,
                CSHandoff.conversation_id == conversation_id,
                CSHandoff.handoff_state != "closed",
            )
            .values(
                handoff_state="closed",
                closed_at=datetime.now(timezone.utc),
            )
        )
        await self._s.flush()
        return result.rowcount > 0

    async def has_active(self, user_id: str) -> bool:
        result = await self._s.execute(
            select(
                exists().where(
                    CSHandoff.user_id == user_id,
                    CSHandoff.handoff_state != "closed",
                )
            )
        )
        return bool(result.scalar())

    async def get_active(self, user_id: str) -> CSHandoff | None:
        result = await self._s.execute(
            select(CSHandoff).where(
                CSHandoff.user_id == user_id,
                CSHandoff.handoff_state != "closed",
            ).limit(1)
        )
        return result.scalar_one_or_none()
