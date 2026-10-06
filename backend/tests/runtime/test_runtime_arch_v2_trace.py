"""STOP G：Runtime 维度必须进入请求 Trace，并可被成本/评测侧消费。"""

from __future__ import annotations

from backend.observability.tracer import trace_collector
from backend.orchestration.router.projection import build_route_decision_v2_from_route
from backend.orchestration.router.router_trace import (
    RUNTIME_TRACE_FIELDS,
    record_router_decision,
    record_runtime_attribution,
)


def setup_function():
    trace_collector.clear_for_test()


def teardown_function():
    trace_collector.clear_for_test()


def test_route_runtime_dimensions_are_complete_and_structured():
    trace = trace_collector.start("规划旅行", session_id="trace-g")
    trace.tags["prompt_version"] = "travel.llm_intent=3"
    decision = build_route_decision_v2_from_route(
        "travel", confidence=0.93, reason="prefilter"
    ).model_copy(update={"workflow_name": "travel"})
    record_router_decision(
        {"domain": "travel", "subflow": "planning", "confidence": 0.93, "source": "prefilter"},
        {"capability": "travel.poi_search", "confidence": 0.93, "source": "prefilter"},
        {"mode": "workflow"},
        {"intent": "travel", "source": "prefilter"},
        route_decision_v2=decision,
    )

    runtime = trace.metadata["runtime"]
    assert set(RUNTIME_TRACE_FIELDS) <= set(runtime)
    assert runtime["domain"] == "travel"
    assert runtime["runtime_type"] == "workflow_runtime"
    assert runtime["runtime_id"] == "travel"
    assert runtime["interaction_mode"] == "execute"
    assert runtime["execution_mode"] == "workflow"
    assert runtime["workflow_id"] == "travel"
    assert runtime["capability"] == "travel.poi_search"
    assert runtime["prompt_version"] == "travel.llm_intent=3"
    assert trace.tags["runtime_domain"] == "travel"
    assert trace.tags["runtime_execution_mode"] == "workflow"


def test_skill_tool_and_domain_attribution_is_retained_for_cost_and_eval():
    trace_collector.start("执行查询", session_id="trace-attribution")
    record_runtime_attribution(
        skill_id="rag", tool_id="search.query", agent_domain="travel"
    )
    runtime = trace_collector.current().metadata["runtime"]
    assert runtime["skill_id"] == "rag"
    assert runtime["tool_id"] == "search.query"
    assert runtime["domain"] == "travel"

