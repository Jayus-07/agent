"""test_trace_data_quality.py — 2026-09-03 Trace 数据记录优化 单测

覆盖（对齐优化方案编号）：
  P0-1  状态聚合引入 rejected（error > rejected > success）
  P0-2  span_id 唯一化 / 未关闭 span 强制收尾
  P1-6  span → 子 trace 关联（child_trace_ids）
  P1-7  毫秒时间戳 / sequence trace 内局部化
  P1-8  skipped 折叠 / rejection 单一事实源瘦身
  P0-4  root span 归因度量（covered_ms / uncovered_ms）
"""
import time

import pytest

from backend.observability.tracer import Span, TraceCollector, TraceRecord, _current_trace_var


@pytest.fixture(autouse=True)
def _reset_contextvar():
    _current_trace_var.set(None)
    yield
    _current_trace_var.set(None)


def _mk_record(**kw) -> TraceRecord:
    rec = TraceRecord(id=kw.pop("id", "t1"), question="q")
    for k, v in kw.items():
        setattr(rec, k, v)
    return rec


# ==========================================================
# P0-1: 状态聚合
# ==========================================================

class TestAggregateStatus:
    def test_rejected_from_metadata(self):
        rec = _mk_record()
        rec.metadata = {"rejection": {"rejected": True, "layer": "retrieval"}}
        rec.spans = [Span(span_id="root", parent_id=None, name="r", type="agent")]
        assert TraceCollector._aggregate_status(rec) == "rejected"

    def test_rejected_from_span_status(self):
        rec = _mk_record()
        rec.spans = [Span(span_id="root", parent_id=None, name="r", type="agent"),
                     Span(span_id="gate", parent_id="root", name="g",
                          type="retrieval_gate", status="rejected")]
        assert TraceCollector._aggregate_status(rec) == "rejected"

    def test_rejected_wins_over_span_error(self):
        rec = _mk_record()
        rec.metadata = {"rejection": {"rejected": True}}
        rec.spans = [Span(span_id="root", parent_id=None, name="r",
                          type="agent", status="error")]
        assert TraceCollector._aggregate_status(rec) == "rejected"

    def test_finish_marks_rejected_trace(self):
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root", parent_id=None, name="r")
        tc.end_span(root)
        t.metadata = {"rejection": {"rejected": True, "layer": "retrieval"}}
        tc.finish(t, "知识库暂无相关资料。", total_ms=10, model="m")
        assert t.status == "rejected"
        # compute_metrics 可读性：rejected 状态进入统计口径
        tc.clear_for_test()

    def test_finish_success_trace_unchanged(self):
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root", parent_id=None, name="r")
        tc.end_span(root)
        tc.finish(t, "ans", total_ms=10, model="m")
        assert t.status == "success"
        tc.clear_for_test()


# ==========================================================
# P0-2: span_id 唯一化 + 泄漏收尾
# ==========================================================

class TestSpanIdUniqueness:
    def test_duplicate_span_id_gets_suffix(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root", parent_id=None, name="r")
        s1 = tc.start_span("enhanced_hybrid_retrieval", name="检索")
        s2 = tc.start_span("enhanced_hybrid_retrieval", name="检索")
        s3 = tc.start_span("enhanced_hybrid_retrieval", name="检索")
        assert s1.span_id == "enhanced_hybrid_retrieval"
        assert s2.span_id == "enhanced_hybrid_retrieval#1"
        assert s3.span_id == "enhanced_hybrid_retrieval#2"
        assert s2.metrics["base_span_id"] == "enhanced_hybrid_retrieval"
        tc.clear_for_test()


class TestLeakedSpanClosure:
    def test_unclosed_span_force_closed_and_marked(self):
        rec = _mk_record(total_ms=100)
        leaked = Span(span_id="x", parent_id=None, name="x", type="retrieval")
        rec.spans = [leaked]
        n = TraceCollector._close_leaked_spans(rec)
        assert n == 1
        assert leaked.end_time != ""
        assert leaked.status == "leaked"
        assert leaked.metrics["unclosed"] is True

    def test_closed_span_untouched(self):
        rec = _mk_record(total_ms=100)
        done = Span(span_id="x", parent_id=None, name="x", type="retrieval",
                    end_time="2026-09-03T00:00:00.000Z", status="success")
        rec.spans = [done]
        assert TraceCollector._close_leaked_spans(rec) == 0
        assert done.status == "success"

    def test_explicit_status_not_overwritten(self):
        rec = _mk_record(total_ms=100)
        sp = Span(span_id="x", parent_id=None, name="x", type="retrieval",
                  status="error")
        rec.spans = [sp]
        TraceCollector._close_leaked_spans(rec)
        assert sp.status == "error"  # 不覆盖显式状态
        assert sp.metrics["unclosed"] is True


# ==========================================================
# P1-6: span → 子 trace 关联
# ==========================================================

class TestChildTraceLink:
    def test_parent_span_gets_child_trace_ids(self):
        tc = TraceCollector()
        parent = tc.start("q", session_id="s", workflow_name="agent")
        executor = tc.start_span("skill_executor", name="直接执行")
        child = tc.start("q", session_id="s", workflow_name="rag_agent")
        assert child.parent_id == parent.id
        assert parent.children_ids == [child.id]
        assert executor.metrics["child_trace_ids"] == [child.id]
        tc.finish(child, "a", 5, "m")
        tc.finish(parent, "a", 10, "")
        tc.clear_for_test()


# ==========================================================
# P1-7: 毫秒时间戳 / sequence 局部化
# ==========================================================

class TestTimestampAndSequence:
    def test_timestamps_have_milliseconds(self):
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root", parent_id=None, name="r")
        tc.end_span(root)
        assert "." in t.timestamp and t.timestamp.endswith("Z")
        assert "." in root.start_time and "." in root.end_time
        tc.clear_for_test()

    def test_sequence_is_trace_local(self):
        """嵌套子 trace 不再污染父 trace 的 sequence。"""
        tc = TraceCollector()
        parent = tc.start("q", workflow_name="agent")
        tc.start_span("root", parent_id=None, name="r")          # seq 0
        tc.start_span("input_guard", name="g")                   # seq 1
        child = tc.start("q", workflow_name="rag_agent")
        tc.start_span("root", parent_id=None, name="cr")         # child seq 0
        tc.start_span("retrieval", name="检索")                   # child seq 1
        tc.finish(child, "a", 5, "m")
        reporter = tc.start_span("reporter", name="汇总")         # 应为 seq 2
        assert reporter.sequence == 2
        tc.finish(parent, "a", 10, "")
        tc.clear_for_test()


# ==========================================================
# P1-8: skipped 折叠 / rejection 瘦身
# ==========================================================

class TestSkippedFolding:
    def test_skipped_spans_folded_into_root_metrics(self):
        rec = _mk_record(total_ms=100)
        root = Span(span_id="root", parent_id=None, name="r", type="agent")
        skipped = Span(span_id="query_rewrite", parent_id="root", name="LLM改写",
                       type="llm_call", status="skipped", metrics={"variants": 0})
        rec.spans = [root, skipped]
        TraceCollector._fold_skipped_spans(rec)
        assert len(rec.spans) == 1
        stages = root.metrics["skipped_stages"]
        assert stages[0]["span_id"] == "query_rewrite"
        assert stages[0]["metrics"] == {"variants": 0}


class TestRejectionSlim:
    def test_root_metrics_slimmed_to_single_source(self):
        rec = _mk_record(total_ms=100)
        rec.metadata = {"rejection": {"rejected": True, "layer": "retrieval",
                                      "reason": "no_evidence"}}
        root = Span(span_id="root", parent_id=None, name="r", type="agent",
                    metrics={"rejected": True, "reason": "no_evidence",
                             "gate_layer": "retrieval", "span_count": 14})
        rec.spans = [root]
        TraceCollector._slim_rejection_dup(rec)
        assert root.metrics == {"rejected": True, "span_count": 14}

    def test_non_rejected_trace_untouched(self):
        rec = _mk_record(total_ms=100)
        root = Span(span_id="root", parent_id=None, name="r", type="agent",
                    metrics={"span_count": 3})
        rec.spans = [root]
        TraceCollector._slim_rejection_dup(rec)
        assert root.metrics == {"span_count": 3}


# ==========================================================
# P0-4: root 归因度量
# ==========================================================

class TestCoverageMetrics:
    def test_uncovered_time_attributed(self):
        rec = _mk_record(total_ms=23000)
        root = Span(span_id="root", parent_id=None, name="r", type="workflow")
        child1 = Span(span_id="a", parent_id="root", name="a", type="tool_call",
                      duration_ms=10000)
        nested = Span(span_id="b", parent_id="a", name="b", type="tool_call",
                      duration_ms=3000)  # 嵌套不计入顶层覆盖
        rec.spans = [root, child1, nested]
        uncovered = TraceCollector._record_coverage(rec)
        assert uncovered == 13000
        assert root.metrics["covered_ms"] == 10000
        assert root.metrics["uncovered_ms"] == 13000

    def test_no_root_returns_zero(self):
        rec = _mk_record(total_ms=100)
        rec.spans = []
        assert TraceCollector._record_coverage(rec) == 0


# ==========================================================
# 集成：finish() 全流程（泄漏收尾 + 折叠 + 状态聚合）
# ==========================================================

class TestFinishIntegration:
    def test_finish_pipeline_on_rejected_trace(self):
        tc = TraceCollector()
        t = tc.start("退款审核时间是多少？", workflow_name="rag_agent")
        root = tc.start_span("root", parent_id=None, name="RAG 智能问答")
        tc.start_span("query_rewrite", name="LLM改写")  # 不 end → 泄漏+skipped? 先 end 为 skipped
        tc.end_span(t.spans[-1], status="skipped", metrics={"variants": 0})
        leaked = tc.start_span("enhanced_hybrid_retrieval", name="检索")  # 不关闭
        tc.end_span(root)
        t.metadata = {"rejection": {"rejected": True, "layer": "retrieval",
                                    "reason": "no_evidence"}}
        tc.finish(t, "知识库暂无相关资料。", total_ms=3365, model="m")

        assert t.status == "rejected"
        ids = [s.span_id for s in t.spans]
        assert "query_rewrite" not in ids  # skipped 已折叠
        assert t.spans[0].metrics.get("skipped_stages")
        leaked_span = next(s for s in t.spans if s.span_id == "enhanced_hybrid_retrieval")
        assert leaked_span.status == "leaked"
        assert leaked_span.end_time != ""
        # root 归因；rejection 详情只存于 metadata（单一事实源），
        # 且 _slim 不会无中生有地给 root 补 rejected 字段。
        root_out = t.spans[0]
        assert "uncovered_ms" in root_out.metrics
        assert "rejected" not in root_out.metrics
        assert t.metadata["rejection"]["rejected"] is True
        tc.clear_for_test()

    def test_slim_runs_inside_finish_when_root_has_rejected(self):
        """模拟 chain._reject 在 root metrics 写入 rejected 后的 finish 行为。"""
        tc = TraceCollector()
        t = tc.start("q", workflow_name="rag_agent")
        root = tc.start_span("root", parent_id=None, name="r")
        tc.end_span(root, metrics={"rejected": True, "reason": "no_evidence",
                                   "gate_layer": "retrieval"})
        t.metadata = {"rejection": {"rejected": True, "layer": "retrieval",
                                    "reason": "no_evidence"}}
        tc.finish(t, "知识库暂无相关资料。", total_ms=100, model="m")
        assert t.status == "rejected"
        assert t.spans[0].metrics["rejected"] is True
        assert "gate_layer" not in t.spans[0].metrics
        assert "reason" not in t.spans[0].metrics
        tc.clear_for_test()
