"""SessionRepository — async CRUD for chat_sessions + chat_messages"""
from sqlalchemy import select, insert, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.memory.models.session import ChatSession, ChatMessage
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

    async def list_all(self, user_id: str = "default", limit: int = 50) -> list[dict]:
        """列出用户的所有会话（id + 标题 + 消息数 + 时间）"""
        from sqlalchemy import desc
        q = (
            select(
                ChatSession.session_id,
                ChatSession.user_id,
                ChatSession.summary,
                ChatSession.context_summary,
                ChatSession.created_at,
                ChatSession.updated_at,
                func.count(ChatMessage.id).label("message_count"),
            )
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.session_id)
            .where(ChatSession.user_id == user_id)
            .group_by(ChatSession.id)
            .order_by(desc(ChatSession.updated_at))
            .limit(limit)
        )
        result = await self._s.execute(q)
        rows = result.all()
        return [
            {
                "session_id": row.session_id,
                "title": row.summary or "新对话",
                "message_count": row.message_count,
                "context_summary": row.context_summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    async def delete(self, session_id: str) -> bool:
        """删除会话（级联删除 messages）"""
        result = await self._s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    async def rename(self, session_id: str, title: str) -> bool:
        """重命名会话（写 summary 字段）"""
        result = await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(summary=title, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def update_context(self, session_id: str, context: str) -> bool:
        """更新 Agent 工作上下文（JSON: sql_results/rag_docs/report/turns）"""
        result = await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(context_summary=context, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def get_context(self, session_id: str) -> str | None:
        """读取会话的上下文摘要"""
        result = await self._s.execute(
            select(ChatSession.context_summary).where(ChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        return row or None
