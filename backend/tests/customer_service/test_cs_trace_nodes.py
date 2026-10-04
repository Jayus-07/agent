"""客服域核心节点 Trace 契约测试。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.observability.tracer import SpanKind, trace_collector


def _start_root_trace():
    trace = trace_collector.start(
        "客服 Trace 测试",
        session_id="cs-trace-test",
        workflow_name="agent",
    )
    trace_collector.start_span("root", parent_id=None, type="workflow")
    return trace


def test_core_cs_node_wrapper_emits_domain_span_with_route_fields():
    from backend.customer_service.trace import wrap_cs_node

    trace = _start_root_trace()
    wrapped = wrap_cs_node(
        "cs_supervisor",
        lambda state: {"supervisor_decision": {"next_action": "finish"}},
    )

    result = wrapped(
        {
            "cs_route": {
                "domain": "TRANSACTION",
                "intent": "t_order_status",
                "route_path": "business_query",
            }
        }
    )

    assert result["supervisor_decision"]["next_action"] == "finish"
    span = next(s for s in trace.spans if s.span_id.startswith("cs_node:cs_supervisor"))
    assert span.name == "客服调度"
    assert span.type == "agent"
    assert span.kind == SpanKind.CS_SUPERVISOR.value
    assert span.status == "success"
    assert span.input == {
        "domain": "customer_service",
        "node": "cs_supervisor",
        "intent": "t_order_status",
        "route_path": "business_query",
    }
    assert span.metrics["domain"] == "customer_service"
    assert span.output == {"decision_action": "finish"}


def test_core_cs_node_wrapper_closes_error_span_without_user_payload():
    from backend.customer_service.trace import wrap_cs_node

    trace = _start_root_trace()

    def explode(_state):
        raise RuntimeError("订单 MO-SECRET-123 的内部失败")

    with pytest.raises(RuntimeError, match="MO-SECRET-123"):
        wrap_cs_node("cs_reporter", explode)({"cs_route": {}})

    span = next(s for s in trace.spans if s.span_id.startswith("cs_node:cs_reporter"))
    assert span.kind == SpanKind.CS_REPORTER.value
    assert span.status == "error"
    assert span.metrics["error_type"] == "RuntimeError"
    assert "MO-SECRET-123" not in str(span.input)
    assert "MO-SECRET-123" not in str(span.output)


def test_cs_trace_context_stamps_root_tags_and_metadata():
    from backend.orchestration.graph.cs_graph_node import _stamp_cs_trace_context

    trace = _start_root_trace()
    _stamp_cs_trace_context(
        {
            "cs_target": "cs_business_query",
            "cs_route": {
                "domain": "TRANSACTION",
                "intent": "t_order_status",
                "confidence": 0.92,
                "route_path": "business_query",
            },
        }
    )

    assert trace.tags["domain"] == "customer_service"
    assert trace.tags["cs_intent"] == "t_order_status"
    assert trace.tags["cs_route_path"] == "business_query"
    assert trace.tags["cs_target"] == "cs_business_query"
    assert trace.metadata["cs_route"] == {
        "domain": "TRANSACTION",
        "intent": "t_order_status",
        "confidence": 0.92,
        "route_path": "business_query",
        "target": "cs_business_query",
    }
    assert "user_message" not in trace.metadata["cs_route"]


def test_cs_graph_wraps_core_nodes_but_not_expert_hooks(monkeypatch):
    from backend.customer_service import graph_builder

    wrapped_names: list[str] = []

    def spy(name, fn):
        wrapped_names.append(name)
        return fn

    monkeypatch.setattr(graph_builder, "wrap_cs_node", spy)
    graph_builder.build_cs_graph()

    assert wrapped_names == [
        "cs_state_loader",
        "cs_pending_handler",
        "cs_supervisor",
        "cs_reporter",
    ]
