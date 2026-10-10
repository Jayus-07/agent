"""旅游链路必须写入 runtime_domain，避免 trace 来源被误判。"""
from __future__ import annotations

from backend.observability.trace_source import SOURCE_TRAVEL, classify_trace_source
from backend.observability.tracer import trace_collector


def test_stamp_execution_tags_sets_runtime_domain():
    """完成路径写入 runtime_domain=travel，分类器据此归为旅游域。"""
    from backend.orchestration.graph.travel_graph_node import _stamp_execution_tags

    trace_collector.clear_for_test()
    trace = trace_collector.start("规划杭州三日游", session_id="travel-domain")
    try:
        _stamp_execution_tags(
            {"conversation_id": "c-1"},
            {"status": "success", "final_answer": "行程已生成"},
            run_id="travel-x",
        )
    finally:
        trace_collector.clear_for_test()

    assert trace.tags["runtime_domain"] == "travel"
    assert classify_trace_source("agent", trace.tags) == SOURCE_TRAVEL


def test_cancel_path_declares_runtime_domain():
    """取消路径也必须声明运行时域。"""
    import inspect

    from backend.orchestration.graph import travel_graph_node as mod

    src = inspect.getsource(mod._maybe_cancel_active_run)
    assert 'trace.tags["travel_status"] = "cancelled"' in src
    assert 'trace.tags["runtime_domain"] = "travel"' in src


def test_travel_status_instrumentation_has_runtime_domain():
    """旅游状态埋点所在的函数都必须设置 runtime_domain。"""
    import inspect

    from backend.orchestration.graph import travel_graph_node as mod

    functions = (mod._maybe_cancel_active_run, mod._stamp_execution_tags)
    for function in functions:
        src = inspect.getsource(function)
        assert 'trace.tags["travel_status"]' in src
        assert 'trace.tags["runtime_domain"] = "travel"' in src
