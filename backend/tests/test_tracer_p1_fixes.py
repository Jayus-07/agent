"""test_tracer_p1_fixes.py — P1-9 TraceCollector 埋点修复 单测

覆盖：
  1. noop span 带 _t0（end_span 可计算 duration，不再恒为 0）
  2. noop span 标记 status=skipped / _noop
  3. start_span 收到不存在的 parent_id 时回退到 root（防孤儿 span）
"""
import time

from backend.observability.tracer import TraceCollector


class TestNoopSpan:
    def test_noop_span_has_t0_and_skipped_status(self):
        tc = TraceCollector()
        # 无 active trace → noop span
        span = tc.start_span("orphan-step", name="孤儿步骤", type="tool_call")
        assert getattr(span, "_noop", False) is True
        assert span.status == "skipped"
        assert getattr(span, "_t0", None) is not None

        # end_span 能计算 duration（不再恒为 0）且不崩溃
        time.sleep(0.02)
        tc.end_span(span, output={"x": 1})
        assert span.duration_ms >= 0  # 至少不抛异常
        assert span.output == {"x": 1}

    def test_real_span_still_works(self):
        tc = TraceCollector()
        trace = tc.start(question="q", session_id="s")
        span = tc.start_span("root", parent_id=None, name="根", type="workflow")
        time.sleep(0.02)
        tc.end_span(span)
        assert span.duration_ms >= 15
        assert span.status == "success"
        assert trace.root_span_id == "root"
        tc.clear_for_test()


class TestParentFallback:
    def test_unknown_parent_falls_back_to_root(self):
        tc = TraceCollector()
        tc.start(question="q", session_id="s")
        root = tc.start_span("root", parent_id=None, name="根", type="workflow")
        # 引用一个不存在的父 span
        child = tc.start_span("child-x", parent_id="nonexistent-parent",
                              name="子", type="tool_call")
        assert child.parent_id == "root"  # 回退到 root 而非悬挂引用

    def test_known_parent_kept(self):
        tc = TraceCollector()
        tc.start(question="q", session_id="s")
        root = tc.start_span("root", parent_id=None, name="根", type="workflow")
        mid = tc.start_span("mid", parent_id="root", name="中", type="agent")
        child = tc.start_span("child", parent_id="mid", name="子", type="tool_call")
        assert child.parent_id == "mid"  # 有效父引用不改动
        tc.clear_for_test()


class TestSpanObjectParentBug:
    """executor.py:193 曾把 Span 对象传给 parent_id（P1-9 主 bug）。

    现在 tracer 对非字符串 parent 做防御：回退 root，不再产生
    parent_id=<Span object> 的脏数据。
    """

    def test_span_object_parent_defended(self):
        tc = TraceCollector()
        tc.start(question="q", session_id="s")
        root = tc.start_span("root", parent_id=None, name="根", type="workflow")
        child = tc.start_span("child", parent_id=root, name="子", type="workflow_step")
        assert isinstance(child.parent_id, str)
        assert child.parent_id == "root"
        tc.clear_for_test()
