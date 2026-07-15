"""L2 会话记忆 — PostgreSQL async backend"""
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from backend.llm.llm_factory import llm
from backend.config import SESSION_MAX_MESSAGES
from backend.utils.logger import logger

_SUMMARY_PROMPT = """请用 2-3 句话总结以下对话的核心内容，保留关键实体、数字、决策和结论:

{conversation}

摘要:"""


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

    async def summarize(self) -> str:
        rows = await self._repo.load_messages(self.session_id, limit=SESSION_MAX_MESSAGES)
        conversation = "\n".join(
            f"{'用户' if r.role == 'user' else '助手'}: {r.content}"
            for r in rows[-SESSION_MAX_MESSAGES:]
        )
        try:
            resp = llm.invoke(_SUMMARY_PROMPT.format(conversation=conversation))
            self._summary = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[SessionMemory:{self.session_id}] 摘要失败: {e}")
            self._summary = conversation[:500]
        return self._summary

    @property
    def message_count(self) -> int:
        return self._message_count
