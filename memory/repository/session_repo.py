"""SessionRepository — async CRUD for chat_sessions + chat_messages"""
from sqlalchemy import select, insert, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from memory.models.session import ChatSession, ChatMessage
from datetime import datetime, timezone


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get_or_create(self, session_id: str, user_id: str = "default") -> ChatSession:
        result = await self._s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
        obj = ChatSession(session_id=session_id, user_id=user_id)
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def load_messages(self, session_id: str, limit: int | None = None) -> list[ChatMessage]:
        q = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
        if limit:
            q = q.limit(limit)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def save_message(self, session_id: str, role: str, content: str) -> ChatMessage:
        msg = ChatMessage(session_id=session_id, role=role, content=content)
        self._s.add(msg)
        # touch session updated_at
        await self._s.execute(
            update(ChatSession).where(ChatSession.session_id == session_id).values(updated_at=datetime.now(timezone.utc))
        )
        await self._s.flush()
        return msg

    async def save_turn(self, session_id: str, question: str, answer: str) -> tuple[ChatMessage, ChatMessage]:
        q = await self.save_message(session_id, "user", question)
        a = await self.save_message(session_id, "assistant", answer)
        return q, a

    async def message_count(self, session_id: str) -> int:
        result = await self._s.execute(
            select(func.count()).where(ChatMessage.session_id == session_id)
        )
        return result.scalar() or 0

    async def needs_summarization(self, session_id: str, max_messages: int = 50) -> bool:
        count = await self.message_count(session_id)
        return count >= max_messages
