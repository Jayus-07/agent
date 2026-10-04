# -*- coding: utf-8 -*-
"""统一 Tool Trace 的契约测试。"""

import pytest

from backend.app.api.routes import admin_tools
from backend.core.tool_runtime.models import ToolResult, ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy
from backend.core.tool_runtime.executor import SafeToolExecutor
from backend.observability import tracer
from backend.observability.tracer import trace_collector


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def _start_trace():
    return trace_collector.start(
        "测试 Tool 失败", session_id="trace-session", workflow_name="agent",
    )


def test_tool_span_records_canonical_error_fields_and_event():
    _start_trace()
    from backend.core.tool_runtime.tracing import finish_tool_span, start_tool_span

    span = start_tool_span(
        "travel_train_search_tool",
        capability="travel.train_search",
        params={"from_station": "北京", "to_station": "上海"},
        agent="travel_supervisor",
    )
    result = ToolResult(
        status=ToolStatus.TIMEOUT,
        tool_name="travel_train_search_tool",
        latency_ms=321,
        error_code="UPSTREAM_TIMEOUT",
        error_message="票务上游响应超时",
    )

    finish_tool_span(span, result)

    assert span.type == "tool_call"
    assert span.kind == "tool"
    assert span.input == {
        "tool": "travel_train_search_tool",
        "capability": "travel.train_search",
        "agent": "travel_supervisor",
        "params": {"from_station": "北京", "to_station": "上海"},
    }
    assert span.status == "error"
    assert span.metrics["error_code"] == "UPSTREAM_TIMEOUT"
    assert span.metrics["error_class"] == "timeout"
    assert any(event["name"] == "tool.error" for event in span.events)


@pytest.mark.asyncio
async def test_executor_creates_one_tool_span_when_caller_has_no_span():
    trace = _start_trace()
    executor = SafeToolExecutor()

    result = await executor.run(
        tool_key="travel.train_search",
        tool_name="travel_train_search_tool",
        trace_capability="travel.train_search",
        trace_agent="travel_supervisor",
        trace_params={"from_station": "北京", "to_station": "上海"},
        call=lambda: "ok",
        policy=ToolPolicy(retries=0, circuit_breaker=False),
    )

    assert result.status is ToolStatus.SUCCESS
    spans = [span for span in trace.spans if span.type == "tool_call"]
    assert len(spans) == 1
    assert spans[0].input["tool"] == "travel_train_search_tool"
    assert spans[0].input["capability"] == "travel.train_search"
    assert spans[0].input["agent"] == "travel_supervisor"
    assert spans[0].status == "success"


@pytest.mark.asyncio
async def test_executor_reuses_supplied_span_without_duplicate():
    trace = _start_trace()
    from backend.core.tool_runtime.tracing import start_tool_span

    span = start_tool_span(
        "sql_query_tool", capability="sql.query", params={"question": "订单数"},
        agent="sql_agent",
    )
    executor = SafeToolExecutor()

    result = await executor.run(
        tool_key="sql.query",
        tool_name="sql_query_tool",
        trace_span=span,
        call=lambda: "ok",
        policy=ToolPolicy(retries=0, circuit_breaker=False),
    )

    assert result.status is ToolStatus.SUCCESS
    spans = [item for item in trace.spans if item.type == "tool_call"]
    assert len(spans) == 1
    assert span.end_time == ""


@pytest.mark.asyncio
async def test_admin_errors_reads_failure_from_span_metrics(monkeypatch):
    trace = _start_trace()
    from backend.core.tool_runtime.tracing import finish_tool_span, start_tool_span

    span = start_tool_span(
        "map_merchant_search_tool", capability="travel.merchant_search",
        params={"near": "未知地点"}, agent="travel_supervisor",
    )
    finish_tool_span(
        span,
        ToolResult(
            status=ToolStatus.UNAVAILABLE,
            tool_name="map_merchant_search_tool",
            latency_ms=88,
            error_code="UPSTREAM_UNAVAILABLE",
            error_message="地图服务不可用",
        ),
    )
    monkeypatch.setattr(admin_tools, "require_admin_user", lambda _request: _noop())
    monkeypatch.setattr(tracer.trace_collector, "list", lambda *_args, **_kwargs: [trace])

    result = await admin_tools.tool_errors(
        None, tool="map_merchant_search_tool", limit=10,
    )

    assert result["count"] == 1
    error = result["errors"][0]
    assert error["error_code"] == "network_error"
    assert error["error_class"] == "network_error"
    assert error["error"] == "地图服务不可用"


async def _noop():
    return None
