"""图节点 Span 归属与状态归因回归。

两个缺陷（2026-10-09 浏览器实测发现）：
  1. 图节点不传 parent_id，走"最近未关闭 span"推断，被挂到同层未收口的
     兄弟 span 下（reporter 画成检索的一部分），耗时归因失真。
  2. 节点正常返回即写 success，掩盖 step_results 里的真实失败。
"""
from __future__ import annotations

from backend.observability.trace_middleware import TraceMiddleware
from backend.observability.tracer import TraceCollector


def test_graph_node_attaches_to_root_not_open_sibling(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.observability.trace_middleware as mw_module

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    monkeypatch.setattr(mw_module, "trace_collector", collector)

    trace = collector.start("问题", "合成会话", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    middleware = TraceMiddleware()

    def rag_node(_state):
        # 节点内部链式埋点（不传 parent_id）应落在本节点之下
        collector.start_span("retrieval", name="检索")
        return {"step_results": {"s1": {"status": "success"}}}

    def reporter_node(_state):
        return {"step_results": {"s2": {"status": "success"}}}

    # rag 节点的 Span 故意不收口，模拟真实链路上层遗留未关闭 span
    wrapped_rag = middleware.wrap_sync_node("rag_skill", rag_node)
    wrapped_rag({"question": "问题"})

    wrapped_reporter = middleware.wrap_sync_node("reporter", reporter_node)
    wrapped_reporter({"question": "问题"})

    spans = {s.span_id: s for s in trace.spans}
    assert spans["retrieval"].parent_id == "rag_skill"
    # 关键：reporter 必须回到 root，而不是 retrieval / rag_skill
    assert spans["reporter"].parent_id == "root"
    collector.clear_for_test()


def test_node_status_reflects_failed_step_result(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.observability.trace_middleware as mw_module

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    monkeypatch.setattr(mw_module, "trace_collector", collector)

    trace = collector.start("问题", "合成会话", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    middleware = TraceMiddleware()
    wrapped = middleware.wrap_sync_node(
        "rag_skill",
        lambda _state: {"step_results": {"s1": {"status": "failed", "error": "服务处理失败"}}},
    )
    wrapped({"question": "问题"})

    node_span = next(s for s in trace.spans if s.span_id == "rag_skill")
    assert node_span.status == "error"
    collector.clear_for_test()


def test_node_status_partial_when_mixed(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.observability.trace_middleware as mw_module

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    monkeypatch.setattr(mw_module, "trace_collector", collector)

    trace = collector.start("问题", "合成会话", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    middleware = TraceMiddleware()
    wrapped = middleware.wrap_sync_node(
        "sql_skill",
        lambda _state: {"step_results": {
            "s1": {"status": "success"},
            "s2": {"status": "timeout"},
        }},
    )
    wrapped({"question": "问题"})

    node_span = next(s for s in trace.spans if s.span_id == "sql_skill")
    assert node_span.status == "partial"
    collector.clear_for_test()


def test_graph_node_binding_reset_after_node(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.observability.trace_middleware as mw_module
    from backend.observability.tracer import get_graph_node

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    monkeypatch.setattr(mw_module, "trace_collector", collector)

    collector.start("问题", "合成会话", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    middleware = TraceMiddleware()
    middleware.wrap_sync_node(
        "rag_skill", lambda _state: {"step_results": {}}
    )({"question": "问题"})

    # 节点结束后绑定必须复位，否则后续无关 span 会被错误吸附
    assert get_graph_node() is None
    collector.clear_for_test()
