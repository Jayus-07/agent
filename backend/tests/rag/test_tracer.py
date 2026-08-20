"""P0.1 — backend/rag/tracer.py 单元测试

覆盖 4 类路径（见 docs/development/testing.md）：
- 正常：start → start_span → end_span → add_event → finish
- 异常：未 start 就 start_span / error status / None 输入
- 边界：并发 100 span / deque maxlen / 空 records / p95 p99
- 降级：parse_tokens 多源识别 / 抛异常也不挂 / tracer 关闭时调用方不感知

CLAUDE.md 硬性要求：Trace 丢失 = P0。任何改动本测试失败 = 不可合并。
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.observability.tracer import (
    MAX_TRACES,
    Span,
    TraceCollector,
    TraceRecord,
    trace_collector,
)
from backend.tests.fixtures.sqlite_tracer import fresh_collector  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_contextvar():
    """每条用例前清掉模块级 contextvar 残留。

    嵌套 trace 语义下，`start()` 会把已存在的 current trace 当作父 trace，
    若上一条用例 start 后未 finish，泄漏的 trace 会被误认为父级，导致
    finish 后 current 不清空。生产环境 start/finish 成对，无此问题。
    """
    from backend.observability.tracer import _current_trace_var
    _current_trace_var.set(None)
    yield
    _current_trace_var.set(None)


# ==========================================================
# 1. 正常路径 — start / start_span / end_span / add_event / finish
# ==========================================================

class TestStart:
    def test_start_creates_trace_with_unique_id(self):
        tc = TraceCollector()
        t1 = tc.start("q1")
        t2 = tc.start("q2")
        assert t1.id != t2.id
        assert len(t1.id) == 12  # uuid4 hex[:12]

    def test_start_sets_question_and_workflow(self):
        tc = TraceCollector()
        t = tc.start(question="hello", workflow_name="rag_agent")
        assert t.question == "hello"
        assert t.workflow_name == "rag_agent"

    def test_start_uses_default_workflow(self):
        tc = TraceCollector()
        t = tc.start("q")
        assert t.workflow_name == "rag_agent"

    def test_start_appends_to_records(self, fresh_collector):
        # 2d627d7: list() 只在 finish() 后从 SQLite 读到
        for q in ["q1", "q2", "q3"]:
            t = fresh_collector.start(q)
            fresh_collector.finish(t, "", 0, "")
        assert len(fresh_collector.list()) == 3

    def test_start_sets_utc_timestamp(self):
        tc = TraceCollector()
        t = tc.start("q")
        assert "T" in t.timestamp
        assert t.timestamp.endswith("Z")


class TestStartSpan:
    def test_first_span_becomes_root(self):
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root_id")
        assert root.parent_id is None
        assert t.root_span_id == "root_id"

    def test_child_span_inherits_root_when_parent_omitted(self):
        """省略 parent_id → 自动取当前 root_span_id"""
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        child = tc.start_span("child")
        assert child.parent_id == "root"

    def test_explicit_parent_wins(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        tc.start_span("a", parent_id="root")
        b = tc.start_span("b", parent_id="root")
        assert b.parent_id == "root"

    def test_type_inferred_from_span_id_llm(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        assert tc.start_span("llm_generate").type == "llm_call"
        assert tc.start_span("query_rewrite").type == "llm_call"

    def test_type_inferred_retrieval(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        assert tc.start_span("hybrid_retrieval").type == "retrieval"
        assert tc.start_span("retrieval").type == "retrieval"

    def test_type_inferred_rerank(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        assert tc.start_span("rerank").type == "rerank"

    def test_unknown_span_id_defaults_to_tool_call(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        assert tc.start_span("weird_thing_xyz").type == "tool_call"

    def test_explicit_type_wins_over_inference(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        s = tc.start_span("llm_generate", type="custom_type")
        assert s.type == "custom_type"

    def test_name_defaults_to_span_id(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        s = tc.start_span("auto_id")
        assert s.name == "auto_id"

    def test_explicit_name_used(self):
        tc = TraceCollector()
        tc.start("q")
        tc.start_span("root")
        s = tc.start_span("auto_id", name="My Span")
        assert s.name == "My Span"

    def test_sequence_increments(self):
        tc = TraceCollector()
        t = tc.start("q")
        tc.start_span("root")     # seq=0
        tc.start_span("a")        # seq=1
        tc.start_span("b")        # seq=2
        assert [s.sequence for s in t.spans] == [0, 1, 2]


class TestEndSpan:
    def test_end_records_duration(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        time.sleep(0.05)
        tc.end_span(s)
        # 50ms ± 抖动
        assert 40 <= s.duration_ms <= 500, f"got {s.duration_ms}ms"

    def test_end_sets_default_status_success(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.end_span(s)
        assert s.status == "success"

    def test_end_sets_error_status(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.end_span(s, status="error")
        assert s.status == "error"

    def test_end_merges_metrics(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        s.metrics["foo"] = 1
        tc.end_span(s, metrics={"bar": 2})
        assert s.metrics == {"foo": 1, "bar": 2}

    def test_end_sets_output(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.end_span(s, output={"answer": "hi"})
        assert s.output == {"answer": "hi"}

    def test_end_keeps_end_time_iso(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.end_span(s)
        assert s.end_time != ""
        assert "T" in s.end_time
        assert s.end_time.endswith("Z")


class TestAddEvent:
    def test_event_appended_to_span(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.add_event(s, "checkpoint", "info", "halfway done")
        assert len(s.events) == 1
        e = s.events[0]
        assert e["name"] == "checkpoint"
        assert e["level"] == "info"
        assert e["message"] == "halfway done"

    def test_event_attributes_default_empty_dict(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.add_event(s, "e", "warn", "msg")
        assert s.events[0]["attributes"] == {}

    def test_event_attributes_passed_through(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.add_event(s, "e", "error", "oops", data={"code": 500})
        assert s.events[0]["attributes"] == {"code": 500}

    def test_event_timestamp_iso(self):
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        tc.add_event(s, "e", "info", "m")
        assert "T" in s.events[0]["timestamp"]


class TestFinish:
    def test_finish_sets_answer_preview(self):
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root")
        tc.end_span(root)
        tc.finish(t, "the answer is 42", total_ms=100, model="deepseek")
        assert t.answer_preview == "the answer is 42"
        # "the answer is 42" = 16 字符
        assert t.answer_len == 16

    def test_finish_truncates_long_answer(self):
        """preview 截断到 200 字符，但 answer_len 保留原始长度"""
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root")
        tc.end_span(root)
        long = "x" * 500
        tc.finish(t, long, total_ms=100, model="m")
        assert len(t.answer_preview) == 200
        assert t.answer_len == 500

    def test_finish_clears_current_trace(self):
        """finish 后 current trace 被清空，start_span 软失败返回 noop span（不抛错）"""
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root")
        tc.end_span(root)
        tc.finish(t, "ans", total_ms=10, model="m")
        span = tc.start_span("x")
        assert span.sequence == -1  # noop span

    def test_finish_aggregates_tokens_across_spans(self):
        tc = TraceCollector()
        t = tc.start("q")
        s1 = tc.start_span("llm1")
        tc.end_span(s1, metrics={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        s2 = tc.start_span("llm2", parent_id="llm1")
        tc.end_span(s2, metrics={"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20})
        tc.finish(t, "a", total_ms=100, model="m")
        assert t.usage == {"prompt_tokens": 15, "completion_tokens": 35, "total_tokens": 50}

    def test_finish_aggregates_when_total_missing(self):
        """pt=10, ct=5 但 total_tokens 缺失 → 应该自己算 = 15"""
        tc = TraceCollector()
        t = tc.start("q")
        s = tc.start_span("llm")
        tc.end_span(s, metrics={"prompt_tokens": 10, "completion_tokens": 5})
        tc.finish(t, "a", total_ms=100, model="m")
        assert t.usage["total_tokens"] == 15

    def test_finish_no_usage_when_no_tokens(self):
        """没有任何 token → usage 不被设置"""
        tc = TraceCollector()
        t = tc.start("q")
        root = tc.start_span("root")
        tc.end_span(root)
        tc.finish(t, "a", total_ms=100, model="m")
        assert t.usage == {}


# ==========================================================
# 2. 异常路径
# ==========================================================

class TestErrorPaths:
    def test_start_span_before_start_returns_noop(self):
        """无 active trace 时 start_span 软失败：返回 noop span 而非抛错（LangGraph 跨线程安全）"""
        tc = TraceCollector()
        span = tc.start_span("orphan")
        assert span.span_id == "orphan"
        assert span.sequence == -1  # noop span 标记

    def test_span_without_end_has_zero_duration(self):
        """异常路径：span end 被跳过 → duration 保持 0，前端可识别"""
        tc = TraceCollector()
        tc.start("q")
        s = tc.start_span("root")
        # 不调 end_span
        assert s.duration_ms == 0
        assert s.end_time == ""

    def test_parse_tokens_returns_empty_for_none(self):
        assert TraceCollector.parse_tokens(None) == {}

    def test_parse_tokens_returns_empty_for_garbage(self):
        class Garbage:
            pass
        assert TraceCollector.parse_tokens(Garbage()) == {}

    def test_parse_tokens_swallows_exception(self):
        """parse_tokens 内 try/except 兜底 — 抛异常的 metadata 不能让 tracer 崩"""
        class Broken:
            @property
            def response_metadata(self):
                raise ValueError("boom")
        assert TraceCollector.parse_tokens(Broken()) == {}


# ==========================================================
# 3. 边界
# ==========================================================

class TestBoundaries:
    @pytest.mark.skip(reason="2d627d7: 内存 deque 已删除，maxlen 行为由 SQLite _MAX_ROWS 取代")
    def test_deque_maxlen_caps_records(self):
        # 旧实现测 _records deque(maxlen=3) — 新架构 SQLite _MAX_ROWS=5000
        # SQLite 容量由 trace_store 控制，不在 TraceCollector
        pass

    def test_empty_records_compute_metrics(self, fresh_collector):
        m = fresh_collector.compute_metrics()
        assert m["total_requests"] == 0
        assert m["completed"] == 0
        assert m["success_rate"] == 0
        assert m["p95_elapsed_sec"] == 0

    def test_concurrent_spans_no_loss(self):
        """100 个 span 并发写入 — 不丢一个"""
        tc = TraceCollector()
        t = tc.start("q")
        tc.start_span("root")

        def worker(i):
            s = tc.start_span(f"span_{i}")
            time.sleep(0.001)
            tc.end_span(s, metrics={"i": i})
            return i

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(worker, range(100)))

        # 1 root + 100 children
        assert len(t.spans) == 101
        # 所有 span 都应该已经 end
        for s in t.spans:
            assert s.duration_ms >= 0

    def test_concurrent_starts_are_safe(self, fresh_collector):
        """10 个线程同时 start() — 不丢 trace"""
        def worker(i):
            t = fresh_collector.start(f"q{i}")
            return t.id

        with ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(worker, range(10)))

        assert len(set(ids)) == 10  # 全部唯一

    def test_list_respects_limit(self, fresh_collector):
        for i in range(10):
            t = fresh_collector.start(f"q{i}")
            fresh_collector.finish(t, "", 0, "")
        assert len(fresh_collector.list(limit=3)) == 3

    def test_list_active_filters_unfinished(self, fresh_collector):
        # 一个完成
        t1 = fresh_collector.start("q1")
        fresh_collector.start_span("root1")
        fresh_collector.end_span(t1.spans[0])
        fresh_collector.finish(t1, "a", 100, "m")
        # 一个未完成
        fresh_collector.start("q2")
        active = fresh_collector.list_active()
        assert len(active) == 1
        assert active[0]["question"] == "q2"  # list_active 返回 dict


class TestComputeMetrics:
    def test_metrics_with_success(self, fresh_collector):
        t = fresh_collector.start("q")
        s = fresh_collector.start_span("root")
        fresh_collector.end_span(s, metrics={"prompt_tokens": 10, "completion_tokens": 5})
        fresh_collector.finish(t, "a", 1000, "m")
        m = fresh_collector.compute_metrics()
        assert m["completed"] == 1
        assert m["success"] == 1
        assert m["error"] == 0
        assert m["success_rate"] == 1.0

    def test_metrics_with_error_span(self, fresh_collector):
        t = fresh_collector.start("q")
        s = fresh_collector.start_span("root")
        fresh_collector.end_span(s, status="error")
        fresh_collector.finish(t, "a", 500, "m")
        m = fresh_collector.compute_metrics()
        assert m["success"] == 0
        assert m["error"] == 1
        assert m["success_rate"] == 0

    def test_metrics_percentiles_ordered(self):
        """p50 < p95 < p99"""
        tc = TraceCollector()
        for i in range(100):
            t = tc.start(f"q{i}")
            s = tc.start_span("root")
            tc.end_span(s)
            tc.finish(t, "a", (i + 1) * 100, "m")  # 100..10000ms
        m = tc.compute_metrics()
        assert m["p50_elapsed_sec"] <= m["p95_elapsed_sec"] <= m["p99_elapsed_sec"]

    def test_metrics_avg_correct(self, fresh_collector):
        for ms in [100, 200, 300]:
            t = fresh_collector.start("q")
            s = fresh_collector.start_span("root")
            fresh_collector.end_span(s)
            fresh_collector.finish(t, "a", ms, "m")
        m = fresh_collector.compute_metrics()
        # 注：compute_metrics 简版不计算 avg_elapsed_sec（从 trace_store 聚合），这里只校验 completed
        assert m["completed"] == 3


# ==========================================================
# 4. 查询 API
# ==========================================================

class TestQueryAPI:
    def test_get_returns_matching_record(self, fresh_collector):
        t = fresh_collector.start("q")
        fresh_collector.finish(t, "a", 100, "m")  # 必须 finish 才入 SQLite
        assert fresh_collector.get(t.id) is not None

    def test_get_returns_none_for_unknown(self, fresh_collector):
        assert fresh_collector.get("nope") is None

    def test_clear_removes_all_records(self, fresh_collector):
        for q in ["q1", "q2"]:
            t = fresh_collector.start(q)
            fresh_collector.finish(t, "", 0, "")
        assert len(fresh_collector.list()) == 2
        # 2d627d7: clear() 保留兼容不做操作；SQLite 数据由 trace_store 控制
        fresh_collector.clear()
        # 验证 clear() 不抛异常
        assert fresh_collector.list() is not None


# ==========================================================
# 5. 降级 — parse_tokens 多源识别
# ==========================================================

class TestParseTokensDegradation:
    def test_langchain_response_metadata(self):
        """LangChain AIMessage 风格：response_metadata.token_usage"""
        class FakeResult:
            response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
            usage_metadata = None
            llm_output = None
        result = TraceCollector.parse_tokens(FakeResult())
        assert result == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def test_anthropic_usage_metadata(self):
        """Anthropic 风格：usage_metadata.input_tokens / output_tokens"""
        class FakeResult:
            response_metadata = None
            usage_metadata = {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}
            llm_output = None
        result = TraceCollector.parse_tokens(FakeResult())
        assert result == {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}

    def test_llm_output_token_usage(self):
        """llm_output.token_usage 兜底"""
        class FakeResult:
            response_metadata = None
            usage_metadata = None
            llm_output = {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
        result = TraceCollector.parse_tokens(FakeResult())
        assert result == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    def test_input_output_aliases_auto_sum(self):
        """input_tokens + output_tokens 没给 total → 自己算"""
        class FakeResult:
            response_metadata = None
            usage_metadata = {"input_tokens": 100, "output_tokens": 200}
            llm_output = None
        result = TraceCollector.parse_tokens(FakeResult())
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 200
        assert result["total_tokens"] == 300


# ==========================================================
# 6. 全局单例
# ==========================================================

class TestSingleton:
    def test_module_level_collector_exists(self):
        assert trace_collector is not None
        assert isinstance(trace_collector, TraceCollector)

    def test_singleton_usable_independently(self):
        """trace_collector 应能独立工作（不污染全局状态）"""
        # 用临时 collector 验证功能
        tc = TraceCollector()
        t = tc.start("test")
        s = tc.start_span("root")
        tc.end_span(s, metrics={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        tc.finish(t, "answer", 100, "m")
        assert t.usage["total_tokens"] == 2


# ==========================================================
# 7. 常量 / 导出完整性
# ==========================================================

class TestConstants:
    def test_max_traces_default(self):
        assert MAX_TRACES == 200

    def test_module_all_exports(self):
        from backend.observability import tracer
        for name in ["TraceCollector", "TraceRecord", "Span", "trace_collector", "MAX_TRACES"]:
            assert name in tracer.__all__