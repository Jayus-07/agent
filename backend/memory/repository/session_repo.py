"""SessionRepository — async CRUD for chat_sessions + chat_messages"""
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.memory.models.session import ChatMessage, ChatSession


class SessionOwnerMismatch(Exception):
    """session_id 已存在但属主（user_id）不同。

    Platform Readiness STOP C（C4 修复）：此前任何用户可用他人 session_id
    「收养」会话——读取其全部 L2 历史并把新消息写入同一历史（chat_messages
    仅按 session_id 存取）。调用方（MemoryService）捕获本异常后派生
    隔离存储键重试，实现无 schema 变更的属主隔离。
    """


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get_or_create(self, session_id: str, user_id: str = "default") -> ChatSession:
        result = await self._s.execute(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        if row:
            if user_id and row.user_id != user_id:
                raise SessionOwnerMismatch(
                    f"session {session_id} 属主为 {row.user_id!r}，"
                    f"与请求身份 {user_id!r} 不符")
            return row
        obj = ChatSession(session_id=session_id, user_id=user_id)
        self._s.add(obj)
        await self._s.flush()
        return obj

    async def load_messages(self, session_id: str, limit: int | None = None) -> list[ChatMessage]:
        """读取会话消息，按时间升序返回。

        带 limit 时取「最近 N 条」（先倒序取再反转），而不是最早的 N 条 ——
        语义修正：上下文注入和摘要只关心最近的对话，旧实现会把最早的消息
        当成"最近历史"喂给模型。
        """
        q = select(ChatMessage).where(ChatMessage.session_id == session_id)
        if limit:
            recent = await self._s.execute(
                q.order_by(ChatMessage.created_at.desc()).limit(limit)
            )
            return list(recent.scalars().all())[::-1]
        result = await self._s.execute(q.order_by(ChatMessage.created_at))
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
        # 首轮自动设标题（仅当 title 为空，不覆盖用户重命名）
        await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id, ChatSession.title.is_(None))
            .values(title=question[:128])
        )
        return q, a

    async def replace_messages(
        self, session_id: str, messages: list[dict[str, str]],
    ) -> list[ChatMessage]:
        """原子替换会话消息快照，并清除已失效的摘要水位线。"""
        await self._s.execute(
            delete(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(
                summary=None,
                summary_through_message_id=None,
                summary_token_count=None,
                summary_updated_at=None,
            )
        )
        saved = []
        first_user_text = ""
        for message in messages:
            role = message["role"]
            content = message["content"]
            saved.append(await self.save_message(session_id, role, content))
            if role == "user" and not first_user_text:
                first_user_text = content
        if first_user_text:
            await self._s.execute(
                update(ChatSession)
                .where(ChatSession.session_id == session_id,
                       ChatSession.title.is_(None))
                .values(title=first_user_text[:128])
            )
        return saved

    async def message_count(self, session_id: str) -> int:
        result = await self._s.execute(
            select(func.count()).where(ChatMessage.session_id == session_id)
        )
        return result.scalar() or 0

    async def needs_summarization(self, session_id: str, max_messages: int = 50) -> bool:
        count = await self.message_count(session_id)
        return count >= max_messages

    async def list_all(self, user_id: str = "default", limit: int = 50,
                       before: str | None = None,
                       before_session_id: str | None = None) -> list[dict]:
        """列出用户的所有会话（id + 标题 + 消息数 + 时间）。

        Args:
            limit: 最多返回 N 条（默认 50；内部允许 201 条用于判断下一页）
            before: 游标分页 ISO timestamp
            before_session_id: 时间相同时作为次级游标，避免会话翻页遗漏
        """
        from sqlalchemy import and_, desc, or_
        limit = max(1, min(limit, 201))
        sort_updated_at = func.coalesce(ChatSession.updated_at, ChatSession.created_at)
        q = (
            select(
                ChatSession.session_id,
                ChatSession.user_id,
                ChatSession.title,
                ChatSession.summary,
                ChatSession.context_summary,
                ChatSession.created_at,
                sort_updated_at.label("updated_at"),
                func.count(ChatMessage.id).label("message_count"),
            )
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.session_id)
            .where(ChatSession.user_id == user_id)
            .group_by(ChatSession.id)
            .order_by(desc(sort_updated_at), desc(ChatSession.session_id))
            .limit(limit)
        )
        if before:
            try:
                from datetime import datetime as _dt
                cursor = _dt.fromisoformat(before.replace("Z", "+00:00"))
                if before_session_id:
                    q = q.where(or_(
                        sort_updated_at < cursor,
                        and_(
                            sort_updated_at == cursor,
                            ChatSession.session_id < before_session_id,
                        ),
                    ))
                else:
                    # 兼容只传时间戳的旧分页调用。
                    q = q.where(sort_updated_at < cursor)
            except (ValueError, AttributeError):
                # 非法 cursor 静默忽略 → 退化为第一页
                pass
        result = await self._s.execute(q)
        rows = result.all()
        return [
            {
                "session_id": row.session_id,
                # title 优先（独立标题字段）；summary 兜底兼容未跑 002 迁移的旧库
                "title": row.title or row.summary or "新对话",
                "message_count": row.message_count,
                "context_summary": row.context_summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    async def count_sessions(self, user_id: str = "default") -> int:
        """统计用户会话总数，配合 list_all 用于 hasMore 判断"""
        result = await self._s.execute(
            select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
        )
        return int(result.scalar() or 0)

    async def get_owner(self, session_id: str) -> str | None:
        """查询会话归属（user_id）。会话不存在返回 None。

        供路由层做属主校验：跨用户访问与「会话不存在」同语义返回 404，
        不向调用方泄露该 session_id 是否真实存在。
        """
        result = await self._s.execute(
            select(ChatSession.user_id).where(ChatSession.session_id == session_id)
        )
        return result.scalar_one_or_none()

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
        """重命名会话（写独立 title 字段，不再占用 summary —— 那是 L2 摘要的位置）"""
        result = await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(title=title[:128], updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def update_summary(self, session_id: str, summary: str) -> bool:
        """持久化 L2 自动摘要（消息数超阈后的压缩结果）"""
        result = await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(summary=summary, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    # ── L5 AutoCompact 摘要水位线（2026-09-22 Phase 3，migration 040）──

    async def get_summary_state(self, session_id: str) -> dict:
        """读取摘要水位线状态（summary / through_id / token_count）。"""
        result = await self._s.execute(
            select(ChatSession.summary, ChatSession.summary_through_message_id,
                   ChatSession.summary_token_count)
            .where(ChatSession.session_id == session_id)
        )
        row = result.first()
        if not row:
            return {"summary": None, "through_id": None, "token_count": None}
        return {"summary": row[0], "through_id": row[1], "token_count": row[2]}

    async def summarizable_before_id(
        self, session_id: str, keep_recent_turns: int,
    ) -> int | None:
        """最近 keep_recent_turns 轮（user 消息开轮）之前的边界消息 id。

        None = 轮数不足，无可增量摘要范围。最近 N 轮永远保持原文。
        """
        result = await self._s.execute(
            select(ChatMessage.id)
            .where(ChatMessage.session_id == session_id,
                   ChatMessage.role == "user")
            .order_by(ChatMessage.id.desc())
            .offset(max(0, int(keep_recent_turns) - 1))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def load_messages_since(
        self, session_id: str, after_id: int, before_id: int, limit: int = 200,
    ) -> list[ChatMessage]:
        """读取水位线区间 (after_id, before_id) 的消息（id 升序）。"""
        result = await self._s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id,
                   ChatMessage.id > int(after_id or 0),
                   ChatMessage.id < int(before_id))
            .order_by(ChatMessage.id.asc())
            .limit(int(limit))
        )
        return list(result.scalars().all())

    async def update_summary_state(
        self, session_id: str, summary: str, through_id: int, token_count: int,
    ) -> bool:
        """持久化增量摘要 + 推进水位线（原子：同一条 UPDATE）。"""
        result = await self._s.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(summary=summary,
                    summary_through_message_id=int(through_id),
                    summary_token_count=int(token_count),
                    summary_updated_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc))
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
