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


def test_l1_turn_limit_keeps_tool_messages_with_their_turn():
    buffer = ShortTermBuffer(max_messages=20, max_turns=2)
    messages = [
        HumanMessage(content="第一轮", id="user-1"),
        AIMessage(content="第一轮回答", id="assistant-1"),
        HumanMessage(content="第二轮", id="user-2"),
        AIMessage(content="", tool_calls=[{
            "name": "lookup", "args": {}, "id": "call-2",
        }], id="assistant-call-2"),
        ToolMessage(content="工具结果", tool_call_id="call-2", id="tool-2"),
        AIMessage(content="第二轮回答", id="assistant-2"),
        HumanMessage(content="第三轮", id="user-3"),
        AIMessage(content="第三轮回答", id="assistant-3"),
    ]
    for message in messages:
        buffer.add(message)

    assert buffer.turn_count == 2
    assert [message.id for message in buffer.get_all()] == [
        "user-2", "assistant-call-2", "tool-2", "assistant-2",
        "user-3", "assistant-3",
    ]


def test_l1_message_limit_evicts_whole_old_turns():
    buffer = ShortTermBuffer(max_messages=3, max_turns=10)
    for index in range(3):
        buffer.add(HumanMessage(content=f"问题{index}", id=f"user-{index}"))
        buffer.add(AIMessage(content=f"回答{index}", id=f"assistant-{index}"))

    assert len(buffer) == 2
    assert buffer.turn_count == 1
    assert [message.id for message in buffer.get_all()] == [
        "user-2", "assistant-2",
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


def test_memory_service_keeps_repeated_text_from_distinct_l2_rows(monkeypatch):
    """同一用户连续重复发送相同内容时，L1 保留不同数据库消息身份。"""
    from backend.memory import service as service_mod

    rows = [
        SimpleNamespace(id=51, role="user", content="好的"),
        SimpleNamespace(id=52, role="assistant", content="收到"),
        SimpleNamespace(id=53, role="user", content="好的"),
        SimpleNamespace(id=54, role="assistant", content="收到"),
    ]

    class Repository:
        async def get_or_create(self, *_args, **_kwargs):
            return SimpleNamespace(summary=None)

        async def message_count(self, _session_id):
            return len(rows)

        async def load_messages(self, _session_id, limit=None):
            assert limit is not None
            return rows

    class DatabaseSession:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    class DatabaseContext:
        async def __aenter__(self):
            return DatabaseSession()

        async def __aexit__(self, *_args):
            return False

    class Retriever:
        last_stage_counts = {}
        embedding = SimpleNamespace(embed_query=lambda _query: [0.0])

        def __init__(self, _repository):
            pass

        async def retrieve(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(service_mod, "AsyncSessionLocal", DatabaseContext)
    monkeypatch.setattr(service_mod, "SessionRepository", lambda _db: Repository())
    monkeypatch.setattr(service_mod, "MemoryRepository", lambda _db: object())
    monkeypatch.setattr(service_mod, "HybridRetriever", Retriever)

    buffer = asyncio.run(service_mod.MemoryService().start_session(
        "session-repeated-text", user_id="user-1", query="好的",
    ))

    assert [type(message) for message in buffer.get_all()] == [
        HumanMessage, AIMessage, HumanMessage, AIMessage,
    ]
    assert [message.id for message in buffer.get_all()] == [
        "chat-message:51", "chat-message:52",
        "chat-message:53", "chat-message:54",
    ]
