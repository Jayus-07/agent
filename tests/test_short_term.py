"""ShortTermBuffer 测试 — L1 环形缓冲区"""
import pytest
from langchain_core.messages import HumanMessage, AIMessage

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.short_term import ShortTermBuffer


class TestShortTermBuffer:
    """L1 短期缓冲基本操作"""

    def test_empty_buffer(self):
        buf = ShortTermBuffer(max_messages=10)
        assert len(buf) == 0
        assert buf.get_all() == []

    def test_add_single_message(self):
        buf = ShortTermBuffer(max_messages=10)
        msg = HumanMessage(content="你好")
        buf.add(msg)
        assert len(buf) == 1
        assert buf.get_all()[0].content == "你好"

    def test_add_turn(self):
        buf = ShortTermBuffer(max_messages=10)
        buf.add_turn("问题", "答案")
        assert len(buf) == 2
        assert isinstance(buf.get_all()[0], HumanMessage)
        assert isinstance(buf.get_all()[1], AIMessage)

    def test_fifo_overflow(self):
        """超出容量后旧消息被淘汰"""
        buf = ShortTermBuffer(max_messages=4)

        for i in range(6):
            buf.add(HumanMessage(content=f"msg_{i}"))

        assert len(buf) == 4
        all_msgs = buf.get_all()
        # 保留最近 4 条
        assert [m.content for m in all_msgs] == ["msg_2", "msg_3", "msg_4", "msg_5"]

    def test_get_recent(self):
        buf = ShortTermBuffer(max_messages=10)
        for i in range(8):
            buf.add(HumanMessage(content=f"msg_{i}"))

        recent = buf.get_recent(3)
        assert len(recent) == 3
        assert [m.content for m in recent] == ["msg_5", "msg_6", "msg_7"]

    def test_get_recent_negative_returns_empty(self):
        buf = ShortTermBuffer(max_messages=10)
        buf.add(HumanMessage(content="test"))
        assert buf.get_recent(-1) == []

    def test_get_recent_more_than_buffer(self):
        buf = ShortTermBuffer(max_messages=10)
        buf.add(HumanMessage(content="only"))
        recent = buf.get_recent(100)
        assert len(recent) == 1

    def test_clear(self):
        buf = ShortTermBuffer(max_messages=10)
        buf.add_turn("q", "a")
        buf.clear()
        assert len(buf) == 0

    def test_default_max_messages_from_config(self):
        buf = ShortTermBuffer()
        assert buf._max == 20  # SHORT_TERM_MAX_MESSAGES default

    def test_custom_max_messages(self):
        buf = ShortTermBuffer(max_messages=42)
        assert buf._max == 42

    def test_messages_property(self):
        buf = ShortTermBuffer(max_messages=10)
        msg = HumanMessage(content="test")
        buf.add(msg)
        assert buf.messages == [msg]

    def test_multiple_turns_overflow(self):
        """多次 add_turn 溢出后只保留最近消息"""
        buf = ShortTermBuffer(max_messages=6)
        for i in range(5):
            buf.add_turn(f"q{i}", f"a{i}")
        # 5 turns = 10 messages, max=6, 保留最近6条 = turn2-turns4
        assert len(buf) == 6
        msgs = buf.get_all()
        assert msgs[0].content == "q2"
        assert msgs[-1].content == "a4"

    def test_mixed_human_ai(self):
        buf = ShortTermBuffer()
        buf.add(HumanMessage(content="h1"))
        buf.add(AIMessage(content="a1"))
        buf.add(HumanMessage(content="h2"))
        assert len(buf) == 3
        assert [type(m).__name__ for m in buf.get_all()] == ["HumanMessage", "AIMessage", "HumanMessage"]
