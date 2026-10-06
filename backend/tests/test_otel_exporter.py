"""test_otel_exporter.py — OTel 第二出口（P2-3）。

用 opentelemetry-sdk 自带 InMemorySpanExporter 验证镜像语义，不需要真
collector。覆盖：开关关/endpoint 空 no-op；启用后 end_span → 镜像 span
落队列（属性映射正确）；listener 回调异常软失败不炸自研 trace；初始化
失败软降级。
"""
from __future__ import annotations

import pytest

import backend.observability.otel_exporter as oe
from backend.observability.otel_exporter import install_if_enabled


@pytest.fixture(autouse=True)
def _reset_install_state():
    """每个用例独立安装态（模块级 _installed/_mirror 单例隔离）。"""
    oe._installed = False
    oe._mirror = None
    yield
    oe._installed = False
    oe._mirror = None


@pytest.fixture
def in_memory_exporter():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    return InMemorySpanExporter()


def _make_mirror(monkeypatch, exporter) -> oe.OtelSpanMirror:
    monkeypatch.setattr(oe, "OTEL_TRACE_OTLP_ENABLED", True)
    monkeypatch.setattr(oe, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    return oe.OtelSpanMirror("http://collector:4318", exporter=exporter)


def _fake_span(status: str = "success"):
    """最小自研 Span 替身（只提供 emit 用到的字段）。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        span_id="s-1", parent_id=None, name="路由决策", type="router",
        kind="agent", status=status, duration_ms=12, retry_count=0,
        metrics={"elapsed_ms": 12}, input={"q": "hi"}, output={"ok": 1},
        errors=[],
    )


def _fake_trace():
    from types import SimpleNamespace
    return SimpleNamespace(id="trace-abc")


def test_disabled_is_noop(monkeypatch):
    """开关关：不订阅 listener、不建 mirror。"""
    monkeypatch.setattr(oe, "OTEL_TRACE_OTLP_ENABLED", False)
    from backend.observability.tracer import trace_collector
    listeners_before = len(trace_collector._listeners)
    assert install_if_enabled() is False
    assert oe._mirror is None
    assert len(trace_collector._listeners) == listeners_before


def test_enabled_without_endpoint_is_noop(monkeypatch):
    monkeypatch.setattr(oe, "OTEL_TRACE_OTLP_ENABLED", True)
    monkeypatch.setattr(oe, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
    assert install_if_enabled() is False


def test_install_subscribes_and_mirrors(monkeypatch, in_memory_exporter):
    """启用：订阅 listener；end_span 后镜像 span 带全部属性落 exporter。"""
    monkeypatch.setattr(oe, "OTEL_TRACE_OTLP_ENABLED", True)
    monkeypatch.setattr(oe, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    # install 内部自建 mirror——patch 工厂让它用 in_memory exporter
    real_cls = oe.OtelSpanMirror
    monkeypatch.setattr(
        oe, "OtelSpanMirror",
        lambda endpoint, exporter=None: real_cls(endpoint, exporter=in_memory_exporter),
    )
    from backend.observability.tracer import trace_collector
    listeners_before = len(trace_collector._listeners)
    assert install_if_enabled() is True
    assert len(trace_collector._listeners) == listeners_before + 1

    # 直接调用新订阅的 listener（等价 end_span 尾部回调）
    callback = trace_collector._listeners[-1]
    callback(_fake_trace(), _fake_span())
    # BatchSpanProcessor 异步——flush 后可见
    oe._mirror._provider.force_flush()

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]
    attrs = dict(s.attributes)
    assert s.name == "路由决策"
    assert attrs["agent.trace_id"] == "trace-abc"
    assert attrs["agent.span_id"] == "s-1"
    assert attrs["agent.status"] == "success"
    assert attrs["agent.duration_ms"] == 12
    assert attrs["agent.metrics.elapsed_ms"] == 12
    assert "agent.input" in attrs and "agent.output" in attrs


def test_error_span_sets_error_status(monkeypatch, in_memory_exporter):
    from opentelemetry.trace import StatusCode
    _make_mirror(monkeypatch, in_memory_exporter)
    mirror = oe.OtelSpanMirror("http://x", exporter=in_memory_exporter)
    mirror.emit(_fake_trace(), _fake_span(status="error"))
    mirror._provider.force_flush()
    spans = in_memory_exporter.get_finished_spans()
    assert spans[0].status.status_code == StatusCode.ERROR


def test_listener_callback_soft_fail(monkeypatch, in_memory_exporter):
    """镜像 emit 抛错：listener 吞掉，不影响自研 trace。"""
    _make_mirror(monkeypatch, in_memory_exporter)
    install_if_enabled()
    from backend.observability.tracer import trace_collector
    callback = trace_collector._listeners[-1]

    monkeypatch.setattr(
        oe._mirror, "emit", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    callback(_fake_trace(), _fake_span())  # 不抛即通过


def test_init_failure_degrades(monkeypatch):
    """OTel 初始化失败：软降级返回 False，不抛。"""
    monkeypatch.setattr(oe, "OTEL_TRACE_OTLP_ENABLED", True)
    monkeypatch.setattr(oe, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://x")

    def _boom(*a, **kw):
        raise ImportError("opentelemetry missing")

    monkeypatch.setattr(oe, "OtelSpanMirror", _boom)
    assert install_if_enabled() is False
    assert oe._mirror is None


def test_idempotent_install(monkeypatch, in_memory_exporter):
    _make_mirror(monkeypatch, in_memory_exporter)
    assert install_if_enabled() is True
    listeners_after_first = None
    from backend.observability.tracer import trace_collector
    listeners_after_first = len(trace_collector._listeners)
    assert install_if_enabled() is True  # 二次调用不重复订阅
    assert len(trace_collector._listeners) == listeners_after_first


# ── 显式 ID 映射（2026-10-06 Tempo 接入）：瀑布聚合与日志关联的前提 ──

def _fake_span_with_parent(span_id: str, parent_id: str | None):
    from types import SimpleNamespace
    return SimpleNamespace(
        span_id=span_id, parent_id=parent_id, name=span_id, type="tool_call",
        kind="tool", status="success", duration_ms=10, retry_count=0,
        metrics={}, input=None, output=None, errors=[],
    )


def test_explicit_ids_group_waterfall(monkeypatch, in_memory_exporter):
    """同 trace 的 span 共享 OTel trace_id，parent 链接成立；hex trace id
    整数值等价映射（Tempo 侧左补零展示）。"""
    from types import SimpleNamespace
    _make_mirror(monkeypatch, in_memory_exporter)
    mirror = oe.OtelSpanMirror("http://x", exporter=in_memory_exporter)
    trace = SimpleNamespace(id="9f86d08112ab")  # uuid4.hex[:12] 形态
    mirror.emit(trace, _fake_span_with_parent("root-span", None))
    mirror.emit(trace, _fake_span_with_parent("child-span", "root-span"))
    mirror._provider.force_flush()

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 2
    root, child = spans
    assert root.parent is None
    assert root.context.trace_id == child.context.trace_id
    assert root.context.trace_id == int("9f86d08112ab", 16)
    assert child.parent.span_id == root.context.span_id


def test_mapping_deterministic():
    """同一字符串映射恒等（parent_id ↔ span_id 对齐的根基）；非 hex 兜底。"""
    assert oe._otel_span_id("router") == oe._otel_span_id("router")
    assert oe._otel_span_id("router") != oe._otel_span_id("router#2")
    assert oe._otel_trace_id("trace-abc") == oe._otel_trace_id("trace-abc")
    # 合法 hex 全零 → 兜底（OTel 禁止全零 trace id）
    assert oe._otel_trace_id("000000000000") == oe._otel_trace_id("000000000000")


def test_duration_reflected_in_timing(monkeypatch, in_memory_exporter):
    """duration_ms 映射为 span 时序跨度（瀑布相对序按真实耗时排布）。"""
    from types import SimpleNamespace
    _make_mirror(monkeypatch, in_memory_exporter)
    mirror = oe.OtelSpanMirror("http://x", exporter=in_memory_exporter)
    span = SimpleNamespace(
        span_id="s", parent_id=None, name="s", type="tool_call",
        kind="tool", status="success", duration_ms=1000, retry_count=0,
        metrics={}, input=None, output=None, errors=[])
    mirror.emit(SimpleNamespace(id="9f86d08112ab"), span)
    mirror._provider.force_flush()
    s = in_memory_exporter.get_finished_spans()[0]
    assert s.end_time - s.start_time == 1_000_000_000
