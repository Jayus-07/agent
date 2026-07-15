"""
L1 短期记忆 — 当前 ask() 调用内的消息缓冲区。

容量受 SHORT_TERM_MAX_MESSAGES 限制（默认 20 条 = 10 轮对话）。
超出后旧消息自动淘汰，不触发摘要（摘要由 L2 会话层负责）。
"""

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from backend.config import SHORT_TERM_MAX_MESSAGES


class ShortTermBuffer:
    """固定容量的消息环形缓冲区"""

    def __init__(self, max_messages: int | None = None):
        self._messages: list[BaseMessage] = []
        self._max = max_messages or SHORT_TERM_MAX_MESSAGES

    def add(self, msg: BaseMessage) -> None:
        self._messages.append(msg)
        if len(self._messages) > self._max:
            self._messages = self._messages[-self._max:]

    def add_turn(self, question: str, answer: str) -> None:
        self.add(HumanMessage(content=question))
        self.add(AIMessage(content=answer))

    def get_all(self) -> list[BaseMessage]:
        return list(self._messages)

    def get_recent(self, n: int) -> list[BaseMessage]:
        return self._messages[-n:] if n > 0 else []

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def messages(self) -> list[BaseMessage]:
        return self._messages
