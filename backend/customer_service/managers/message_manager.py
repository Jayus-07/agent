"""MessageManager — async CRUD for CS messages

Handles message creation, persistence, and type distinction
(user / assistant / system / human_agent).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func, update, desc as desc_col
from sqlalchemy.ext.asyncio import AsyncSession

from backend.customer_service.models.message import CSMessage
from backend.customer_service.models.conversation import CSConversation


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MessageManager:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def create(
        self,
        conversation_id: str,
        content: str,
        sender_type: str = "user",
        *,
        message_id: str | None = None,
        sender_id: str | None = None,
        content_type: str = "text",
        intent_domain: str | None = None,
        intent_name: str | None = None,
        confidence: float | None = None,
        private: bool = False,
        metadata: dict | None = None,
        attachments: list | None = None,
    ) -> CSMessage:
        msg = CSMessage(
            message_id=message_id or uuid.uuid4().hex,
            conversation_id=conversation_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content=content,
            content_type=content_type,
            intent_domain=intent_domain,
            intent_name=intent_name,
            confidence=confidence,
            private=private,
            metadata_=metadata or {},
            attachments=attachments,
        )
        self._s.add(msg)

        await self._s.execute(
            update(CSConversation)
            .where(CSConversation.conversation_id == conversation_id)
            .values(last_activity_at=_now(), updated_at=_now())
        )

        await self._s.flush()
        return msg

    async def save_user_message(
        self, conversation_id: str, content: str, **kw,
    ) -> CSMessage:
        return await self.create(
            conversation_id, content, sender_type="user", **kw,
        )

    async def save_assistant_message(
        self, conversation_id: str, content: str, **kw,
    ) -> CSMessage:
        return await self.create(
            conversation_id, content, sender_type="assistant", **kw,
        )

    async def save_system_message(
        self, conversation_id: str, content: str, *, private: bool = True, **kw,
    ) -> CSMessage:
        return await self.create(
            conversation_id, content, sender_type="system",
            private=private, **kw,
        )

    async def save_human_agent_message(
        self, conversation_id: str, content: str,
        sender_id: str, **kw,
    ) -> CSMessage:
        return await self.create(
            conversation_id, content, sender_type="human_agent",
            sender_id=sender_id, **kw,
        )

    async def save_turn(
        self, conversation_id: str, question: str, answer: str,
        *,
        intent_domain: str | None = None,
        intent_name: str | None = None,
        confidence: float | None = None,
    ) -> tuple[CSMessage, CSMessage]:
        q = await self.save_user_message(conversation_id, question)
        a = await self.save_assistant_message(
            conversation_id, answer,
            intent_domain=intent_domain,
            intent_name=intent_name,
            confidence=confidence,
        )
        return q, a

    async def load_messages(
        self, conversation_id: str, *, limit: int | None = None,
        include_private: bool = False,
    ) -> list[CSMessage]:
        q = (
            select(CSMessage)
            .where(CSMessage.conversation_id == conversation_id)
        )
        if not include_private:
            q = q.where(CSMessage.private.is_(False))
        if limit:
            latest_ids = (
                q.order_by(desc_col(CSMessage.created_at))
                .limit(limit)
                .scalar_subquery()
            )
            q = (
                select(CSMessage)
                .where(CSMessage.id.in_(latest_ids))
                .order_by(CSMessage.created_at)
            )
        else:
            q = q.order_by(CSMessage.created_at)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def message_count(self, conversation_id: str) -> int:
        result = await self._s.execute(
            select(func.count())
            .where(CSMessage.conversation_id == conversation_id)
        )
        return int(result.scalar() or 0)

    async def get_last_message(
        self, conversation_id: str,
    ) -> CSMessage | None:
        result = await self._s.execute(
            select(CSMessage)
            .where(CSMessage.conversation_id == conversation_id)
            .order_by(desc_col(CSMessage.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()
