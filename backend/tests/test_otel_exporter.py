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
