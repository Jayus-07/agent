"""
L1 短期记忆 — 当前 ask() 调用内的消息缓冲区。

窗口同时受消息数和对话轮数限制。裁剪以 HumanMessage 开始的新轮为边界，
同一轮内的 AI/Tool 消息一并保留；默认最多 20 条消息、10 轮。
消息数上限用于淘汰旧的完整轮次，最新单轮不拆分，因此可能超过该上限。
超出后旧轮自动淘汰，不触发摘要（摘要由 L2 会话层负责）。
"""

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from backend.config import SHORT_TERM_MAX_MESSAGES, SHORT_TERM_MAX_TURNS


class ShortTermBuffer:
    """按完整轮次裁剪的短期消息缓冲区。"""

    def __init__(self, max_messages: int | None = None,
                 max_turns: int | None = None):
        self._messages: list[BaseMessage] = []
        self._max_messages = max(1, int(
            max_messages if max_messages is not None else SHORT_TERM_MAX_MESSAGES))
        self._max_turns = max(1, int(
            max_turns if max_turns is not None else SHORT_TERM_MAX_TURNS))
        # STOP G observability：最近一次装配的 L3 检索阶段计数
        # （candidate/accepted/rejected/global/final/threshold），
        # 由 runner 侧写入 memory.retrieve span（service 运行在无
        # ambient trace 的后台 loop，span 须由 runner 创建）。
        self.retrieval_stats: dict = {}

    def add(self, msg: BaseMessage) -> None:
        # 同一持久消息可能经恢复/状态合并再次到达；按稳定 ID 去重，
        # 不按正文去重，避免丢掉用户连续发送的相同内容。
        message_id = getattr(msg, "id", None)
        identity = str(message_id).strip() if message_id is not None else ""
        if identity and any(
            getattr(existing, "id", None) is not None
            and str(existing.id) == identity
            for existing in self._messages
        ):
            return
        self._messages.append(msg)
        self._trim_window()

    def _trim_window(self) -> None:
        """按完整用户轮次裁剪，并同时满足轮数和消息数上限。"""
        if not self._messages:
            return

        # 一个 HumanMessage 开始一轮；其后的 AI、Tool 及工具结果都归入该轮。
        # 开头无 HumanMessage 的历史前缀并入第一轮，避免留下孤立的助手尾部。
        turns: list[list[BaseMessage]] = []
        prefix: list[BaseMessage] = []
        current: list[BaseMessage] | None = None
        for message in self._messages:
            if isinstance(message, HumanMessage):
                if current is not None:
                    turns.append(current)
                    current = [message]
                else:
                    current = [*prefix, message]
                    prefix.clear()
            elif current is None:
                prefix.append(message)
            else:
                current.append(message)

        if current is not None:
            turns.append(current)
        elif prefix:
            # 没有用户消息时无法分轮，退回严格消息数上限。
            self._messages = prefix[-self._max_messages:]
            return

        while len(turns) > self._max_turns or (
                len(turns) > 1
                and sum(len(turn) for turn in turns) > self._max_messages):
            turns.pop(0)
        self._messages = [message for turn in turns for message in turn]

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
    def turn_count(self) -> int:
        """当前缓冲区中的用户轮数；纯非用户前缀不计为一轮。"""
        return sum(isinstance(message, HumanMessage)
                   for message in self._messages)

    @property
    def messages(self) -> list[BaseMessage]:
        return self._messages
