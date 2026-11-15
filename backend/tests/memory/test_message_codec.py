"""L2 持久化与恢复 LangChain 文本消息的契约。"""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.context_budget.auto_compact import _format_conversation
from backend.memory.message_codec import restore_message, role_label, serialize_message
from backend.memory.models.session import ChatMessage, ChatSession
from backend.memory.repository.session_repo import SessionRepository
from backend.memory.service import _history_message_payload
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
                sql_results = [{
                    "title": "本月销售额",
                    "columns": ["月份", "销售额"],
                    "rows": [["2026-11", 12345]],
                    "total_rows": 1,
                    "total_rows_known": True,
                    "omitted_columns": 0,
                }]
                answer_metadata = {
                    "sql_results": sql_results,
                    "sources": [{
                        "index": 1, "filename": "员工手册.pdf", "doc_type": "policy",
                        "type_label": "制度", "score": 0.8, "pages": [4],
                        "section": "请假制度", "doc_id": "policy-doc",
                    }],
                    "answer_status": "rag_retrieval_degraded",
                    "confidence": 0.42,
                    "reply_source": "knowledge_base",
                }
                question_row, answer_row = await repository.save_turn(
                    "l2-runner-turn", "用户问题", "最终回答",
                    assistant_metadata=answer_metadata,
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
                assert answer_row.message_metadata["response_metadata"]["sql_results"] == sql_results
                assert answer_row.message_metadata["response_metadata"] == answer_metadata
                assert restored[1].response_metadata == answer_metadata
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


def test_history_api_payload_only_exposes_allowlisted_assistant_metadata():
    from datetime import datetime, timezone

    row = type("HistoryRow", (), {
        "id": 9,
        "role": "assistant",
        "content": "销售额为 12,345。",
        "created_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
        "message_metadata": {
            "response_metadata": {
                "sql_results": [{
                    "title": "销售额", "columns": ["金额（元）"],
                    "rows": [[12345]], "total_rows": 1, "omitted_columns": 0,
                    "total_rows_known": True,
                    "sql": "SELECT secret_column FROM private_table",
                }],
                "sources": [{
                    "index": 1, "filename": "员工手册.pdf", "doc_type": "policy",
                    "type_label": "制度", "score": 0.9, "pages": [4, 0, True, "5"],
                    "section": "请假制度", "doc_id": "policy-doc",
                    "version_id": "v2", "effective_from": "2026-01-01",
                    "effective_to": "2026-12-31",
                    "supersedes_version_id": "internal-only",
                    "private_text": "不要返回原文内容",
                }],
                "answer_status": "rag_retrieval_degraded",
                "confidence": 0.42,
                "reply_source": "knowledge_base",
                "private_internal": "do not expose",
            },
            "other_metadata": "do not expose",
        },
    })()
    payload = _history_message_payload(row)

    assert payload["sql_results"] == [{
        "title": "销售额", "columns": ["金额（元）"],
        "rows": [[12345]], "total_rows": 1, "total_rows_known": True,
        "omitted_columns": 0,
    }]
    assert payload["sources"] == [{
        "index": 1, "filename": "员工手册.pdf", "doc_type": "policy",
        "type_label": "制度", "score": 0.9, "pages": [4],
        "section": "请假制度", "doc_id": "policy-doc",
        "version_id": "v2", "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }]
    assert payload["answer_status"] == "rag_retrieval_degraded"
    assert payload["confidence"] == 0.42
    assert payload["reply_source"] == "knowledge_base"
    assert "private_internal" not in str(payload)
    assert "other_metadata" not in str(payload)
    assert "private_text" not in str(payload)
    assert "supersedes_version_id" not in str(payload)
    assert "secret_column" not in str(payload)


def test_history_api_marks_legacy_sql_total_unknown():
    from datetime import datetime, timezone

    row = type("HistoryRow", (), {
        "id": 12,
        "role": "assistant",
        "content": "查询结果",
        "created_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
        "message_metadata": {
            "response_metadata": {
                "sql_results": [{
                    "title": "订单查询", "columns": ["订单号"],
                    "rows": [], "total_rows": 0, "omitted_columns": 0,
                }],
            },
        },
    })()

    sql_result = _history_message_payload(row)["sql_results"][0]

    assert sql_result["total_rows"] == 0
    assert sql_result["total_rows_known"] is False


def test_history_api_bounds_sql_result_sets_rows_cells_and_payload_size():
    from datetime import datetime, timezone

    columns = [f"列{i}" for i in range(15)]
    rows = [["值" * 200 for _ in columns] for _ in range(25)]
    row = type("HistoryRow", (), {
        "id": 10,
        "role": "assistant",
        "content": "查询结果",
        "created_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
        "message_metadata": {
            "response_metadata": {
                "sql_results": [{
                    "title": "T" * 200,
                    "columns": columns,
                    "rows": rows,
                    "total_rows": 100,
                    "omitted_columns": 0,
                    "internal": "不应返回",
                } for _ in range(4)],
            },
        },
    })()

    sql_results = _history_message_payload(row)["sql_results"]

    assert len(sql_results) <= 3
    assert len(sql_results[0]["title"]) <= 120
    assert len(sql_results[0]["columns"]) == 12
    assert sql_results[0]["omitted_columns"] == 3
    assert len(sql_results[0]["rows"]) <= 20
    assert all(len(cell) <= 160 for row_values in sql_results[0]["rows"]
               for cell in row_values if isinstance(cell, str))
    assert len(json.dumps(sql_results, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
    assert all("internal" not in result for result in sql_results)
    assert any(result.get("payload_truncated") for result in sql_results)


def test_history_api_replaces_nested_sql_cell_with_safe_placeholder():
    from datetime import datetime, timezone

    row = type("HistoryRow", (), {
        "id": 11,
        "role": "assistant",
        "content": "查询结果",
        "created_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
        "message_metadata": {
            "response_metadata": {
                "sql_results": [{
                    "title": "明细", "columns": ["值"],
                    "rows": [[{"private_text": "internal payload"}]],
                    "total_rows": 1, "omitted_columns": 0,
                }],
            },
        },
    })()

    sql_result = _history_message_payload(row)["sql_results"][0]

    assert sql_result["rows"] == [[None]]
    assert sql_result["truncated_cells"] is True
    assert "internal payload" not in str(sql_result)

    row.role = "user"
    user_payload = _history_message_payload(row)
    assert "sources" not in user_payload
    assert "answer_status" not in user_payload
