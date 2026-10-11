"""L2 持久化与恢复 LangChain 文本消息的契约。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.context_budget.auto_compact import _format_conversation
from backend.memory.message_codec import restore_message, role_label, serialize_message
from backend.memory.models.session import ChatMessage, ChatSession
from backend.memory.repository.session_repo import SessionRepository
from backend.memory.session import SessionMemory


def test_langchain_messages_round_trip_through_l2_sqlite():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(ChatSession.__table__.create)
                await connection.run_sync(ChatMessage.__table__.create)

            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as db:
                repository = SessionRepository(db)
                await repository.get_or_create("l2-message-roundtrip", "user-1")
                source_messages = [
                    HumanMessage(content="查杭州天气", id="client-human-id"),
                    AIMessage(
                        content="",
                        id="client-ai-id",
                        tool_calls=[{
                            "name": "weather_lookup",
                            "args": {"city": "杭州", "units": "c"},
                            "id": "call-1",
                            "type": "tool_call",
                        }],
                    ),
                    ToolMessage(
                        content='{"temperature": 25}',
                        tool_call_id="call-1",
                        name="weather_lookup",
                        artifact={"source": "weather-provider", "cached": False},
                    ),
                    AIMessage(
                        content="杭州 25°C，晴。",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 8,
                            "total_tokens": 20,
                        },
                    ),
                ]
                for message in source_messages:
                    await repository.save_langchain_message(
                        "l2-message-roundtrip", message,
                    )
                await db.commit()

                memory = SessionMemory("l2-message-roundtrip")
                memory._repo = repository
                restored = await memory.load_messages()

                assert [type(message) for message in restored] == [
                    HumanMessage, AIMessage, ToolMessage, AIMessage,
                ]
                assert [message.id for message in restored] == [
                    f"chat-message:{row_id}" for row_id in range(1, 5)
                ]
                assert restored[1].tool_calls == source_messages[1].tool_calls
                assert restored[2].tool_call_id == "call-1"
                assert restored[2].name == "weather_lookup"
                assert restored[2].artifact == {
                    "source": "weather-provider", "cached": False,
                }
                assert restored[2].status == "success"
                assert restored[3].usage_metadata == source_messages[3].usage_metadata
                assert role_label("tool") == "工具"
                assert _format_conversation([
                    (5, "tool", "天气查询结果"),
                    (6, "assistant", "已整理结果"),
                ]) == "工具: 天气查询结果\n助手: 已整理结果"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_session_repository_save_turn_uses_typed_l2_messages():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(ChatSession.__table__.create)
                await connection.run_sync(ChatMessage.__table__.create)

            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as db:
                repository = SessionRepository(db)
                await repository.get_or_create("l2-runner-turn", "user-2")
                question_row, answer_row = await repository.save_turn(
                    "l2-runner-turn", "用户问题", "最终回答",
                )
                await db.commit()

                rows = await repository.load_messages("l2-runner-turn")
                memory = SessionMemory("l2-runner-turn")
                memory._repo = repository
                restored = await memory.load_messages()

                assert [row.message_type for row in rows] == ["human", "ai"]
                assert [row.role for row in rows] == ["user", "assistant"]
                assert [type(message) for message in restored] == [
                    HumanMessage, AIMessage,
                ]
                assert [message.id for message in restored] == [
                    f"chat-message:{question_row.id}",
                    f"chat-message:{answer_row.id}",
                ]
                assert [message.content for message in restored] == [
                    "用户问题", "最终回答",
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_legacy_rows_restore_as_human_or_ai_with_database_identity():
    restored = restore_message(
        type("LegacyRow", (), {
            "id": 17, "role": "user", "content": "旧行",
            "message_type": None, "message_metadata": None,
        })()
    )

    assert isinstance(restored, HumanMessage)
    assert restored.id == "chat-message:17"
    assert restored.content == "旧行"


def test_message_codec_rejects_unsupported_or_inconsistent_payloads():
    with pytest.raises(ValueError, match="不支持"):
        serialize_message(SystemMessage(content="不可持久化为 L2 对话"))

    with pytest.raises(ValueError, match="文本 content"):
        serialize_message(HumanMessage(content=[{"type": "text", "text": "多模态"}]))

    bad_row = type("BadRow", (), {
        "id": 18, "role": "assistant", "content": "工具结果",
        "message_type": "tool", "message_metadata": {"tool_call_id": "call-2"},
    })()
    with pytest.raises(ValueError, match="role 与 message_type"):
        restore_message(bad_row)
