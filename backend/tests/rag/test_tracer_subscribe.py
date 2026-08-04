"""P1.5 — TraceCollector.subscribe() 事件订阅测试

Phase 1.5: end_span 时通知所有 listeners（用于 SSE 实时进度推送）。

2d627d7 重构后使用公共 fresh_collector fixture（见 tests/fixtures/sqlite_tracer.py），
避免直接操作已移除的 _records / _active 等模块级属性。
"""

import pytest

from backend.observability.tracer import trace_collector, TraceCollector
from backend.observability import tracer as tracer_mod
from backend.tests.fixtures.sqlite_tracer import fresh_collector  # noqa: F401  (re-export for backwards compat)


def test_subscribe_fires_on_span_end(fresh_collector):
    events = []

    def cb(trace, span):
        events.append((trace.id, span.span_id, span.status))

    unsub = fresh_collector.subscribe(cb)
    trace = fresh_collector.start("test")
    s = fresh_collector.start_span("root")
    fresh_collector.end_span(s)

    assert len(events) == 1
    assert events[0] == (trace.id, "root", "success")


def test_subscribe_fires_multiple_spans(fresh_collector):
    events = []

    def cb(trace, span):
        events.append(span.span_id)

    fresh_collector.subscribe(cb)
    # 2d627d7: 保留 start() 返回的 trace，直接读 trace.spans
    # 旧版通过 list()[0].spans 读内存；新版 list() 只读 SQLite (需 finish())
    trace = fresh_collector.start("t")
    fresh_collector.start_span("a")
    fresh_collector.start_span("b")
    fresh_collector.start_span("c")
    # end_span 触发 listener（最后一个 c 还未 end）
    for sp in trace.spans:
        fresh_collector.end_span(sp)
    # 但 listener 触发顺序按 end_span 调用顺序
    assert "a" in events
    assert "b" in events
    assert "c" in events


def test_unsubscribe_stops_callbacks(fresh_collector):
    events = []

    def cb(trace, span):
        events.append(span.span_id)

    unsub = fresh_collector.subscribe(cb)
    fresh_collector.start("t")
    s1 = fresh_collector.start_span("first")
    fresh_collector.end_span(s1)

    unsub()  # 取消订阅

    s2 = fresh_collector.start_span("second")
    fresh_collector.end_span(s2)

    assert events == ["first"]  # second 不触发


def test_multiple_subscribers_all_fire(fresh_collector):
    events_a, events_b = [], []

    fresh_collector.subscribe(lambda t, s: events_a.append(s.span_id))
    fresh_collector.subscribe(lambda t, s: events_b.append(s.span_id))

    fresh_collector.start("t")
    s = fresh_collector.start_span("shared")
    fresh_collector.end_span(s)

    assert events_a == ["shared"]
    assert events_b == ["shared"]


def test_listener_exception_does_not_break_tracer(fresh_collector):
    """listener 抛异常不能影响 tracer 后续工作"""
    def bad_cb(trace, span):
        raise ValueError("listener boom")

    def good_cb(trace, span):
        pass  # 仍然会注册

    fresh_collector.subscribe(bad_cb)
    fresh_collector.subscribe(good_cb)

    fresh_collector.start("t")
    s = fresh_collector.start_span("test")
    # 不抛异常
    fresh_collector.end_span(s, metrics={"ok": 1})
    # span 正常 end
    assert s.status == "success"
    assert s.metrics == {"ok": 1}


def test_listener_receives_span_metrics_and_status(fresh_collector):
    captured = []

    def cb(trace, span):
        captured.append({
            "trace_id": trace.id,
            "span_id": span.span_id,
            "status": span.status,
            "duration_ms": span.duration_ms,
            "metrics": dict(span.metrics),
        })

    fresh_collector.subscribe(cb)
    fresh_collector.start("t")
    s = fresh_collector.start_span("root")
    fresh_collector.end_span(s, metrics={"chunks": 10, "duration_wall": 5}, status="success")

    assert len(captured) == 1
    assert captured[0]["span_id"] == "root"
    assert captured[0]["status"] == "success"
    assert captured[0]["metrics"]["chunks"] == 10
    assert captured[0]["metrics"]["duration_wall"] == 5
    assert captured[0]["duration_ms"] >= 0


def test_listener_receives_error_status(fresh_collector):
    captured = []

    fresh_collector.subscribe(lambda t, s: captured.append((s.span_id, s.status)))

    fresh_collector.start("t")
    s = fresh_collector.start_span("failed")
    fresh_collector.end_span(s, status="error", metrics={"error": "boom"})

    assert captured == [("failed", "error")]


def test_listener_works_alongside_existing_tracer_features(fresh_collector):
    """subscribe 不影响 list_active / compute_metrics / finish 等已有 API"""
    fresh_collector.subscribe(lambda t, s: None)  # 注册一个空 listener

    trace = fresh_collector.start("t", session_id="s1")
    s = fresh_collector.start_span("root")
    fresh_collector.end_span(s)

    # 已有 API 仍工作
    # 2d627d7: list() 只在 finish() 后从 SQLite 读取；先 finish 再 list
    fresh_collector.finish(trace, "answer", 100, "m")
    records = fresh_collector.list()
    assert len(records) == 1
    assert records[0]["session_id"] == "s1"  # list() 返回 dict 而非 dataclass
    metrics = fresh_collector.compute_metrics()
    assert metrics["completed"] == 1


# ==========================================================
# Phase 1.5 集成：span → SSE stage 映射逻辑（ProgressListener._format_message）
# ==========================================================

class TestProgressMessageFormat:
    """直接验证 ProgressListener._format_message 输出格式"""

    def test_parse_message_includes_doc_count(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_parse", parent_id="index_upload", name="Parse",
                 type="parse", kind="index_parse")
        s.metrics = {"doc_count": 42}
        msg = ProgressListener._format_message(s)
        assert "42" in msg
        assert "页" in msg

    def test_chunk_message_includes_kept_and_filtered(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_chunk", parent_id="index_upload", name="Chunk",
                 type="chunk", kind="index_chunk")
        s.metrics = {"kept_chunks": 100, "filtered_out": 5}
        msg = ProgressListener._format_message(s)
        assert "100" in msg
        assert "5" in msg
        assert "过滤" in msg

    def test_chunk_message_without_filtered(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_chunk", parent_id="index_upload", name="Chunk",
                 type="chunk", kind="index_chunk")
        s.metrics = {"kept_chunks": 50, "filtered_out": 0}
        msg = ProgressListener._format_message(s)
        assert "50" in msg
        assert "过滤" not in msg  # 0 个过滤时不显示

    def test_embed_message_partial_failure(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_embed", parent_id="index_upload", name="Embed",
                 type="embedding", kind="index_embed")
        s.metrics = {"succeeded": 8, "attempted": 10}
        msg = ProgressListener._format_message(s)
        assert "8/10" in msg
        assert "部分失败" in msg

    def test_embed_message_all_success(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_embed", parent_id="index_upload", name="Embed",
                 type="embedding", kind="index_embed")
        s.metrics = {"succeeded": 10, "attempted": 10}
        msg = ProgressListener._format_message(s)
        assert "10/10" in msg
        assert "部分失败" not in msg

    def test_vector_db_message_includes_written_count(self):
        from backend.rag.progress_listener import ProgressListener
        from backend.observability.tracer import Span

        s = Span(span_id="index_vector_db", parent_id="index_upload", name="VDB",
                 type="vector_db", kind="index_vector_db")
        s.metrics = {"written": 100}
        msg = ProgressListener._format_message(s)
        assert "100" in msg
        assert "向量" in msg