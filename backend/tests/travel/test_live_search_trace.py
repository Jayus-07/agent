# -*- coding: utf-8 -*-
"""旅游直调 Tool 的统一 Trace 测试。"""

import json

import pytest

from backend.observability.tracer import trace_collector
from backend.travel.services import live_search_service


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def test_direct_travel_tool_success_writes_canonical_span():
    trace = trace_collector.start("查北京到上海的车票", session_id="travel-1", workflow_name="agent")

    class FakeTool:
        @staticmethod
        def invoke(_kwargs):
            return json.dumps({"status": "success", "data": {"trains": []}})

    raw = live_search_service._invoke(
        FakeTool(), "travel_train_search_tool",
        capability="travel.train.search", agent="planning",
        from_station="北京", to_station="上海", date="2026-10-03", limit=6,
    )

    assert json.loads(raw)["status"] == "success"
    spans = [span for span in trace.spans if span.type == "tool_call"]
    assert len(spans) == 1
    assert spans[0].input["tool"] == "travel_train_search_tool"
    assert spans[0].input["capability"] == "travel.train.search"
    assert spans[0].input["agent"] == "planning"
    assert spans[0].status == "success"


def test_direct_travel_tool_failure_closes_span_with_error():
    trace = trace_collector.start("查不可用车票", session_id="travel-2", workflow_name="agent")

    class FakeTool:
        @staticmethod
        def invoke(_kwargs):
            return json.dumps({
                "status": "failed",
                "error": "12306 MCP 暂时不可用",
                "error_code": "MCP_UNAVAILABLE",
            })

    with pytest.raises(live_search_service.LiveSearchError, match="12306 MCP"):
        live_search_service._invoke(
            FakeTool(), "travel_train_search_tool",
            capability="travel.train.search", agent="planning",
            from_station="北京", to_station="上海", date="2026-10-03", limit=6,
        )
    span = next(span for span in trace.spans if span.type == "tool_call")
    assert span.status == "error"
    assert span.metrics["error_code"] == "upstream_unavailable"
    assert span.metrics["error_class"] == "business_error"
    assert span.end_time


def test_direct_travel_tool_exception_keeps_source_error_code():
    trace_collector.start("查异常车票", session_id="travel-3", workflow_name="agent")

    class FakeTool:
        @staticmethod
        def invoke(_kwargs):
            raise RuntimeError("MCP socket closed")

    with pytest.raises(live_search_service.LiveSearchError, match="MCP socket closed"):
        live_search_service._invoke(
            FakeTool(), "travel_train_search_tool",
            capability="travel.train.search", agent="planning",
            from_station="北京", to_station="上海", date="2026-10-03", limit=6,
        )

    span = next(
        span for span in trace_collector.current().spans
        if span.type == "tool_call"
    )
    assert span.status == "error"
    assert span.metrics["error_code"] == "internal_error"
    assert span.metrics["error_class"] == "business_error"
