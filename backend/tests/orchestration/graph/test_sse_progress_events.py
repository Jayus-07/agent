"""主图在现有 SSE 事件流中输出 RAG 与 SQL 的实时阶段。"""
from __future__ import annotations

import asyncio

import backend.config as config_module
from backend.skills.rag import skill as rag_skill_module
from backend.skills.sql import skill as sql_skill_module
from backend.skills.rag.skill import RAGSkill, rag_skill_node
from backend.skills.sql.skill import SQLSkill
from backend.sql.sql_result import SQLResult
from backend.orchestration.graph.sse_event_sink import (
    bind_sse_event_sink,
    emit_sse_event,
    reset_sse_event_sink,
)


def test_sse_event_sink_is_request_scoped():
    events: list[dict] = []
    token = bind_sse_event_sink(events.append)
    try:
        emit_sse_event({"event": "log", "data": {"step_id": "in-request"}})
    finally:
        reset_sse_event_sink(token)

    emit_sse_event({"event": "log", "data": {"step_id": "after-reset"}})
    assert [event["data"]["step_id"] for event in events] == ["in-request"]


def test_rag_skill_announces_search_before_invoking_tool(monkeypatch):
    seen: list[str] = []
    events: list[dict] = []

    async def fake_execute(self, state, step_capability=""):
        seen.append("execute")
        return {}

    monkeypatch.setattr(RAGSkill, "execute", fake_execute)
    monkeypatch.setattr(
        rag_skill_module,
        "emit_sse_progress",
        lambda **event: (events.append(event), seen.append("event")),
        raising=False,
    )
    asyncio.run(rag_skill_node({"question": "退款怎么处理"}))

    assert seen == ["event", "execute"]
    assert events[0]["phase"] == "rag_search"
    assert events[0]["tool"] == "rag.search"


def test_sql_skill_forwards_request_sse_sink_to_sql_agent(monkeypatch):
    events: list[dict] = []

    class FakeAgent:
        def ask_struct(self, question, *, policy=None, query_context=None, event_sink=None):
            assert event_sink is not None
            event_sink({"phase": "sql_generation", "tool": "sql.query"})
            return SQLResult.success(
                rows=[{"sku": "A"}], columns=["sku"], sql="SELECT 'A' AS sku",
            )

    def fake_policy_context(_state):
        return object()

    monkeypatch.setattr(config_module, "SQL_AGENT_ENABLED", True)
    monkeypatch.setattr(sql_skill_module, "_build_sql_policy_context", fake_policy_context)
    monkeypatch.setattr(sql_skill_module, "get_sql_agent", FakeAgent)
    monkeypatch.setattr(sql_skill_module, "emit_sse_event", events.append, raising=False)
    monkeypatch.setattr(
        "backend.sql.query_context.persist_sql_query_result",
        lambda **_kwargs: None,
    )

    state = {
        "question": "查询商品",
        "plan": {"nodes": {"1": {"step_id": "1", "capability": "sql.query", "description": "查询商品"}}},
        "current_step_id": "1",
        "request_context": {
            "session_id": "sse-progress-test", "user_id": "3",
            "tenant_id": "tenant-test", "department": "ecom", "roles": ["editor"],
        },
    }

    result = asyncio.run(SQLSkill().execute(state))

    assert result["step_results"]["1"]["status"] == "success"
    assert events == [{"phase": "sql_generation", "tool": "sql.query"}]
