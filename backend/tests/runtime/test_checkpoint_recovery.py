# -*- coding: utf-8 -*-
"""STOP D：LangGraph checkpoint 可序列化、恢复与决策字段稳定性。"""

import json
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.core.request_context import RequestContext
from backend.orchestration.request_context import get_context_from_state


class _RuntimeState(TypedDict, total=False):
    n: int
    request_context: dict
    domain_decision: dict
    capability_decision: dict
    execution_decision: dict
    route_mode: str
    route_decision: dict
    query_understanding: dict
    resumed: bool


def _build_graph():
    def first(state: _RuntimeState):
        return {"n": state.get("n", 0) + 1}

    def second(state: _RuntimeState):
        return {"n": state.get("n", 0) + 1}

    def last(state: _RuntimeState):
        return {"n": state.get("n", 0) + 1, "resumed": True}

    builder = StateGraph(_RuntimeState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_node("last", last)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", "last")
    builder.add_edge("last", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.parametrize(
    ("domain", "capability", "execution"),
    [
        ("customer_service", "order_query", "direct"),
        ("customer_service", "refund_query", "workflow"),
        ("travel", "trip_plan", "plan"),
        ("travel", "poi_search", "direct"),
        ("selection", "product_compare", "workflow"),
        ("general", "general_chat", "direct"),
        ("sql", "sql_query", "direct"),
        ("customer_service", "handoff", "workflow"),
        ("general", "fallback", "direct"),
        ("travel", "weather_query", "direct"),
    ],
)
def test_checkpoint_recovery_preserves_runtime_state(domain, capability, execution):
    """中断后恢复只继续未完成节点，且身份/决策状态不漂移。"""
    ctx = RequestContext(
        session_id="conversation-1",
        user_id="user-7",
        tenant_id="tenant-a",
        kb_id="kb-main",
        roles=("viewer",),
        data_scope="department",
        subject_type="employee",
        permissions=("kb.read",),
    )
    initial = {
        "n": 0,
        "request_context": ctx.checkpoint_safe(),
        "domain_decision": {"domain": domain},
        "capability_decision": {"capability": capability},
        "execution_decision": {"mode": execution},
        "route_mode": execution,
        "route_decision": {"domain": domain, "capability": capability},
        "query_understanding": {"normalized_query": "继续执行"},
    }
    graph = _build_graph()
    config = {"configurable": {"thread_id": f"stop-d-{domain}-{capability}"}}

    stream = graph.stream(initial, config=config, stream_mode="updates")
    next(stream)
    next(stream)
    stream.close()

    checkpoint = graph.get_state(config).values
    json.dumps(checkpoint, ensure_ascii=False)
    assert checkpoint["n"] == 2
    assert checkpoint["domain_decision"]["domain"] == domain
    assert checkpoint["capability_decision"]["capability"] == capability
    assert checkpoint["execution_decision"]["mode"] == execution

    restored = get_context_from_state(checkpoint)
    assert restored is not None
    assert restored.user_id == "user-7"
    assert restored.tenant_id == "tenant-a"
    assert restored.roles == ("viewer",)
    assert restored.data_scope == "department"
    assert restored.bind_sink is False

    result = graph.invoke(None, config=config)
    assert result["n"] == 3
    assert result["resumed"] is True
    assert graph.get_state(config).values["route_mode"] == execution
