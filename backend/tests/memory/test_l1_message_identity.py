"""L1 消息身份与类型契约。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.memory.session import SessionMemory
from backend.memory.short_term import ShortTermBuffer


def test_l1_deduplicates_by_stable_id_and_keeps_equal_text_messages():
    buffer = ShortTermBuffer(max_messages=5)
    first = HumanMessage(content="好的", id="message-1")
    repeated_delivery = HumanMessage(content="好的", id="message-1")
    same_text_later = HumanMessage(content="好的", id="message-2")
    tool_result = ToolMessage(
        content="查询完成", tool_call_id="call-1", id="message-3",
    )

    buffer.add(first)
    buffer.add(repeated_delivery)
    buffer.add(same_text_later)
    buffer.add(tool_result)
    buffer.add(tool_result)

    assert [message.id for message in buffer.get_all()] == [
        "message-1", "message-2", "message-3",
    ]
    assert [type(message) for message in buffer.get_all()] == [
        HumanMessage, HumanMessage, ToolMessage,
    ]


def test_session_memory_restores_database_ids_and_human_ai_roles():
    rows = [
        SimpleNamespace(id=41, role="user", content="杭州三日游"),
        SimpleNamespace(id=42, role="assistant", content="好的"),
    ]

    class Repository:
        async def load_messages(self, session_id, limit=None):
            assert session_id == "session-1"
            assert limit == 10
            return rows

    memory = SessionMemory("session-1")
    memory._repo = Repository()
    messages = asyncio.run(memory.load_messages(limit=10))

    assert [type(message) for message in messages] == [HumanMessage, AIMessage]
    assert [message.id for message in messages] == [
        "chat-message:41", "chat-message:42",
    ]
    assert [message.content for message in messages] == ["杭州三日游", "好的"]
