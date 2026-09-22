"""L2 会话记忆 — PostgreSQL async backend"""
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from backend.config import SESSION_MAX_MESSAGES
from backend.shared.logger import logger


class SessionMemory:
    """单个会话的持久化记忆 — backed by PostgreSQL"""

    def __init__(self, session_id: str, user_id: str = "default"):
        self.session_id = session_id
        self.user_id = user_id
        self._summary: str | None = None
        self._repo = None  # set during async init
        self._message_count: int = 0

    @classmethod
    async def create(cls, session_id: str, repo, user_id: str = "default") -> "SessionMemory":
        inst = cls(session_id, user_id)
        inst._repo = repo
        inst._message_count = await repo.message_count(session_id)
        return inst

    async def load_messages(self, limit: int | None = None) -> list[BaseMessage]:
        rows = await self._repo.load_messages(self.session_id, limit=limit)
        return [
            HumanMessage(content=r.content) if r.role == "user"
            else AIMessage(content=r.content)
            for r in rows
        ]

    async def save_turn(self, question: str, answer: str) -> None:
        await self._repo.save_turn(self.session_id, question, answer)
        self._message_count += 2

    @property
    def needs_summarization(self) -> bool:
        return self._message_count >= SESSION_MAX_MESSAGES

    async def summarize(self) -> str | None:
        """生成会话摘要；失败返回 None（调用方不覆盖旧摘要）。

        Phase 3（L5 AutoCompact，2026-09-22）：真实 repo 优先走**增量摘要
        水位线**——只把 summary_through_message_id 之后、最近
        CONTEXT_L4_KEEP_RECENT_TURNS 轮之前的新消息合并进旧摘要，
        不重复总结整段会话；水位线随摘要原子推进。
        fake repo / 增量路径不可用时回退旧全量路径（行为不变）。
        """
        from backend.memory.repository.session_repo import SessionRepository
        if isinstance(self._repo, SessionRepository):
            try:
                import asyncio as _aio
                from backend.context_budget.auto_compact import (
                    SyncMemorySummaryStore,
                    run_incremental_summary,
                )
                outcome = await _aio.to_thread(
                    run_incremental_summary,
                    self.session_id, SyncMemorySummaryStore(self.session_id))
                if outcome is not None:
                    self._summary = outcome.summary
                # outcome None（无增量内容/失败）：沿用现有摘要，不覆盖
                return self._summary
            except Exception as e:
                logger.warning(
                    f"[SessionMemory:{self.session_id}] 增量摘要路径失败，"
                    f"回退全量路径: {e}")

        rows = await self._repo.load_messages(self.session_id, limit=SESSION_MAX_MESSAGES)
        conversation = "\n".join(
            f"{'用户' if r.role == 'user' else '助手'}: {r.content}"
            for r in rows[-SESSION_MAX_MESSAGES:]
        )
        try:
            from backend.infra.llm import llm
            from backend.prompts.service import prompt_service
            r = prompt_service.render_sync("memory.session.summary", conversation=conversation)
            resp = llm.invoke(r.text)
            self._summary = resp.content if hasattr(resp, "content") else str(resp)
            return self._summary
        except Exception as e:
            logger.error(f"[SessionMemory:{self.session_id}] 摘要失败，保留旧摘要: {e}")
            return None

    @property
    def message_count(self) -> int:
        return self._message_count
