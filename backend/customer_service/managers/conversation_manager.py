"""ConversationManager — async CRUD + state transitions for CS conversations

Follows the SessionRepository pattern: takes an AsyncSession, exposes
domain-level methods that combine queries + state machine validation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.state_machine import (
    ConvStatus, HandlingMode, TransitionResult,
    transition, apply,
)

_now = lambda: datetime.now(timezone.utc)


class ConversationManager:
    def __init__(self, session: AsyncSession):
        self._s = session

    # ── CRUD ────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: str,
        channel: str = "web",
        *,
        conversation_id: str | None = None,
        handling_mode: HandlingMode = HandlingMode.AI,
        priority: str = "medium",
        assigned_agent_id: str | None = None,
    ) -> CSConversation:
        conv = CSConversation(
            conversation_id=conversation_id or uuid.uuid4().hex,
            user_id=user_id,
            conversation_status=ConvStatus.OPEN.value,
            handling_mode=handling_mode.value,
            channel=channel,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            last_activity_at=_now(),
        )
        self._s.add(conv)
        await self._s.flush()
        return conv

    async def get(self, conversation_id: str) -> CSConversation | None:
        result = await self._s.execute(
            select(CSConversation).where(
                CSConversation.conversation_id == conversation_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, conversation_id: str, user_id: str,
                            **kw) -> tuple[CSConversation, bool]:
        """Return (conversation, created).  created=True if newly inserted."""
        conv = await self.get(conversation_id)
        if conv:
            return conv, False
        conv = await self.create(user_id, conversation_id=conversation_id, **kw)
        return conv, True

    async def list_by_user(
        self, user_id: str, *, limit: int = 50,
        status: ConvStatus | None = None,
    ) -> list[CSConversation]:
        q = (
            select(CSConversation)
            .where(CSConversation.user_id == user_id)
            .order_by(desc(CSConversation.updated_at))
            .limit(min(limit, 200))
        )
        if status:
            q = q.where(CSConversation.conversation_status == status.value)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def count(self, user_id: str | None = None,
                    status: ConvStatus | None = None) -> int:
        q = select(func.count()).select_from(CSConversation)
        if user_id:
            q = q.where(CSConversation.user_id == user_id)
        if status:
            q = q.where(CSConversation.conversation_status == status.value)
        result = await self._s.execute(q)
        return int(result.scalar() or 0)

    async def delete(self, conversation_id: str) -> bool:
        conv = await self.get(conversation_id)
        if not conv:
            return False
        await self._s.delete(conv)
        await self._s.flush()
        return True

    # ── state transitions ───────────────────────────────────────────

    async def transition(
        self,
        conversation_id: str,
        new_status: ConvStatus | None = None,
        new_mode: HandlingMode | None = None,
    ) -> TransitionResult:
        """Validate + apply a state transition.  Raises on invalid transition."""
        conv = await self.get(conversation_id)
        if not conv:
            from backend.customer_service.errors import ValidationError
            raise ValidationError(f"Conversation {conversation_id} not found")

        result = transition(conv, new_status, new_mode)
        if not result.changed:
            return result

        apply(conv, result)

        if result.conversation_status == ConvStatus.RESOLVED:
            conv.closed_at = _now()

        conv.updated_at = _now()
        await self._s.flush()
        return result

    async def resolve(self, conversation_id: str) -> TransitionResult:
        return await self.transition(
            conversation_id,
            new_status=ConvStatus.RESOLVED,
            new_mode=HandlingMode.AI,
        )

    async def reopen(self, conversation_id: str) -> TransitionResult:
        return await self.transition(
            conversation_id,
            new_status=ConvStatus.OPEN,
        )

    async def escalate_to_human(
        self, conversation_id: str, agent_id: str,
    ) -> TransitionResult:
        conv = await self.get(conversation_id)
        if not conv:
            from backend.customer_service.errors import ValidationError
            raise ValidationError(f"Conversation {conversation_id} not found")

        conv.assigned_agent_id = agent_id
        await self._s.flush()

        result = transition(conv, new_mode=HandlingMode.HUMAN)
        apply(conv, result)
        conv.updated_at = _now()
        await self._s.flush()

        await self._record_assignment(conversation_id, agent_id, assigned_by="system")
        return result

    async def request_human(
        self, conversation_id: str,
    ) -> TransitionResult:
        return await self.transition(
            conversation_id,
            new_mode=HandlingMode.WAITING_HUMAN,
        )

    async def hand_back_to_ai(
        self, conversation_id: str,
    ) -> TransitionResult:
        conv = await self.get(conversation_id)
        if not conv:
            from backend.customer_service.errors import ValidationError
            raise ValidationError(f"Conversation {conversation_id} not found")

        result = transition(conv, new_mode=HandlingMode.AI)
        apply(conv, result)
        conv.assigned_agent_id = None
        conv.updated_at = _now()
        await self._s.flush()
        return result

    # ── assignment ──────────────────────────────────────────────────

    async def assign_agent(
        self, conversation_id: str, agent_id: str,
        assigned_by: str = "system",
    ) -> None:
        conv = await self.get(conversation_id)
        if not conv:
            from backend.customer_service.errors import ValidationError
            raise ValidationError(f"Conversation {conversation_id} not found")

        old_agent = conv.assigned_agent_id
        if old_agent:
            await self._close_assignment(conversation_id, old_agent)

        conv.assigned_agent_id = agent_id
        conv.updated_at = _now()
        await self._s.flush()
        await self._record_assignment(conversation_id, agent_id, assigned_by=assigned_by)

    async def _record_assignment(
        self, conversation_id: str, agent_id: str, assigned_by: str,
    ) -> None:
        record = CSAssignment(
            conversation_id=conversation_id,
            agent_id=agent_id,
            assigned_by=assigned_by,
        )
        self._s.add(record)
        await self._s.flush()

    async def _close_assignment(
        self, conversation_id: str, agent_id: str,
    ) -> None:
        await self._s.execute(
            update(CSAssignment)
            .where(
                CSAssignment.conversation_id == conversation_id,
                CSAssignment.agent_id == agent_id,
                CSAssignment.unassigned_at.is_(None),
            )
            .values(unassigned_at=_now())
        )
        await self._s.flush()

    # ── metadata ────────────────────────────────────────────────────

    async def update_summary(self, conversation_id: str, summary: str) -> bool:
        result = await self._s.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(summary=summary, updated_at=_now())
        )
        await self._s.flush()
        return result.rowcount > 0

    async def update_context(self, conversation_id: str, context: str) -> bool:
        result = await self._s.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(context_summary=context, updated_at=_now())
        )
        await self._s.flush()
        return result.rowcount > 0

    async def set_priority(self, conversation_id: str, priority: str) -> bool:
        result = await self._s.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(priority=priority, updated_at=_now())
        )
        await self._s.flush()
        return result.rowcount > 0

    async def touch_activity(self, conversation_id: str) -> None:
        await self._s.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(last_activity_at=_now(), updated_at=_now())
        )
        await self._s.flush()
