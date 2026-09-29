# -*- coding: utf-8 -*-
"""STOP D：请求、节点与异步任务的 Trace 生命周期与可关联性。"""

from types import SimpleNamespace

import pytest

from backend.core.request_context import RequestContext
from backend.observability.trace_middleware import TraceMiddleware
from backend.observability.tracer import WorkflowKind, trace_collector


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def _start_request():
    RequestContext(
        session_id="conversation-42", user_id="user-42", tenant_id="tenant-42",
    ).bind()
    return trace_collector.start(
        "查询订单", session_id="conversation-42", workflow_name="agent",
        workflow_kind=WorkflowKind.LG_WORKFLOW.value,
    )


def test_request_trace_has_request_session_identity_tags():
    trace = _start_request()
    assert trace.id == trace.request_id
    assert trace.session_id == "conversation-42"
    assert trace.tags["user_id"] == "user-42"
    assert trace.tags["tenant_id"] == "tenant-42"


def test_trace_root_span_closes_on_success():
    trace = _start_request()
    root = trace_collector.start_span("root", parent_id=None, type="workflow")
    trace_collector.end_span(root, status="success")
    trace_collector.finish(trace, "已完成", 12, model="test-model", provider="test")
    assert root.end_time
    assert trace.status == "success"
    assert trace_collector.current() is None


def test_trace_finish_marks_unclosed_span_as_leaked():
    trace = _start_request()
    root = trace_collector.start_span("root", parent_id=None, type="workflow")
    trace_collector.start_span("unfinished", type="tool_call")
    trace_collector.finish(trace, "失败", 4, model="test-model")
    assert root.end_time
    assert any(span.status == "leaked" for span in trace.spans)


def test_nested_trace_restores_parent_after_child_finishes():
    parent = _start_request()
    trace_collector.start_span("parent-root", parent_id=None, type="workflow")
    child = trace_collector.start("子流程", session_id="conversation-42")
    child_root = trace_collector.start_span("child-root", parent_id=None, type="workflow")
    trace_collector.end_span(child_root)
    trace_collector.finish(child, "子流程完成", 3, model="test-model")
    assert trace_collector.current() is parent
    trace_collector.finish(parent, "完成", 8, model="test-model")


def test_trace_middleware_records_node_error_span():
    trace = _start_request()

    def boom(_state):
        raise RuntimeError("上游不可用")

    wrapped = TraceMiddleware().wrap_sync_node("router", boom)
    with pytest.raises(RuntimeError, match="上游不可用"):
        wrapped({"question": "q"})
    span = next(span for span in trace.spans if span.span_id == "router")
    assert span.status == "error"
    assert "上游不可用" in span.metrics["error"]


def test_trace_middleware_records_node_success_span():
    trace = _start_request()
    wrapped = TraceMiddleware().wrap_sync_node("router", lambda _state: {"ok": True})
    assert wrapped({"question": "q"}) == {"ok": True}
    span = next(span for span in trace.spans if span.span_id == "router")
    assert span.status == "success"
    assert span.end_time


def test_task_trace_projects_execution_identity(monkeypatch):
    from backend.orchestration.checkpoint.task_executor import TaskGraphExecutor

    monkeypatch.setattr("backend.services.task_service.set_trace_id", lambda *_args: None)
    executor = TaskGraphExecutor.__new__(TaskGraphExecutor)
    executor._execution_id = "execution-7"
    record = SimpleNamespace(
        id="task-7", thread_id="conversation-7", input={"query": "异步任务"},
        graph_name="agent", queue="agent", retry_count=1, recovery_count=2,
    )
    trace = executor._start_task_trace(record)
    assert trace is not None
    assert trace.session_id == "conversation-7"
    assert trace.tags["task_id"] == "task-7"
    assert trace.tags["execution_id"] == "execution-7"
    assert trace.tags["queue"] == "agent"


def test_trace_span_tree_keeps_parent_relationship():
    trace = _start_request()
    root = trace_collector.start_span("root", parent_id=None, type="workflow")
    child = trace_collector.start_span("tool", name="工具", type="tool_call")
    trace_collector.end_span(child)
    trace_collector.end_span(root)
    assert child.parent_id == root.span_id
    assert child in trace.spans


def test_trace_summary_does_not_store_raw_question_in_span_output():
    trace = _start_request()
    span = trace_collector.start_span(
        "reporter", input={"question": "包含身份证 110101... 的敏感问题"},
    )
    trace_collector.end_span(span, output={"status": "ok"})
    assert span.output == {"status": "ok"}
    assert "身份证" not in str(span.output)


def test_trace_metrics_shape_exposes_latency_percentiles(monkeypatch):
    monkeypatch.setattr(
        "backend.observability.trace_store.get_trace_store",
        lambda: SimpleNamespace(list=lambda _limit: [
            {"duration_ms": 10, "status": "success"},
            {"duration_ms": 20, "status": "success"},
            {"duration_ms": 50, "status": "error"},
        ]),
    )
    metrics = trace_collector.compute_metrics()
    assert metrics["total_requests"] == 3
    assert metrics["p50_elapsed_sec"] >= 0
    assert metrics["p95_elapsed_sec"] >= metrics["p50_elapsed_sec"]
    assert metrics["p99_elapsed_sec"] >= metrics["p95_elapsed_sec"]
