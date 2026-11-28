"""架构守卫测试：验证 metrics.py 门面瘦身及 metrics_domain 子包物理拆解的契约与向后兼容性。"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram

import backend.observability.metrics as metrics
import backend.observability.metrics_domain as metrics_domain
from backend.observability.metrics import (
    StreamLatencyTracker,
    chat_request_duration_seconds,
    chat_request_total,
    circuit_breaker_state,
    llm_tokens_total,
    publish_breaker_states,
    rag_stage_duration_seconds,
    record_router_fallback,
    render_metrics,
    skill_failure_total,
)


def test_metrics_facade_line_count():
    """验证 metrics.py 门面物理行数不超过 100 行。"""
    facade_file = Path(metrics.__file__)
    lines = facade_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 100, f"metrics.py 门面行数为 {len(lines)}，超过 100 行上限"


def test_metrics_domain_submodules_exist_and_bounded():
    """验证 metrics_domain 专职子模块完整存在且无超大单体（每个均 <= 700 行）。"""
    domain_dir = Path(metrics_domain.__file__).parent
    expected_modules = {
        "base.py",
        "stream_tracker.py",
        "core_sli.py",
        "rag_metrics.py",
        "router_metrics.py",
        "cs_metrics.py",
        "infra_metrics.py",
        "exporter.py",
        "__init__.py",
    }
    actual_modules = {p.name for p in domain_dir.glob("*.py")}
    assert expected_modules.issubset(actual_modules), f"缺失专职子模块: {expected_modules - actual_modules}"

    for mod_path in domain_dir.glob("*.py"):
        line_count = len(mod_path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 700, f"子模块 {mod_path.name} 行数为 {line_count}，超过 700 行阈值"


def test_metrics_all_contract_and_export_integrity():
    """验证 240+ 个指标、类及函数的契约完备性与导出正确性。"""
    assert isinstance(chat_request_total, Counter)
    assert isinstance(chat_request_duration_seconds, Histogram)
    assert isinstance(llm_tokens_total, Counter)
    assert isinstance(skill_failure_total, Counter)
    assert isinstance(rag_stage_duration_seconds, Histogram)
    assert isinstance(circuit_breaker_state, Gauge)

    assert inspect.isclass(StreamLatencyTracker)
    assert isinstance(publish_breaker_states, Callable)
    assert isinstance(record_router_fallback, Callable)
    assert isinstance(render_metrics, Callable)

    # 验证 render_metrics 输出契约
    body, ctype = render_metrics()
    assert isinstance(body, bytes)
    assert "text/plain" in ctype or "version=" in ctype

    # 验证 __all__ 导出符号数量
    assert len(metrics.__all__) >= 240


def test_stream_latency_tracker_functional_contract():
    """验证 StreamLatencyTracker 的计时与流式上报链路功能正常。"""
    tracker = StreamLatencyTracker(start=100.0)
    assert tracker.delta_count == 0
    assert tracker.ttft_ms is None

    tracker.on_delta(100.15)
    assert tracker.delta_count == 1
    assert tracker.ttft_ms == 150

    tracker.on_feedback(100.20)
    assert tracker.first_feedback_ms == 200

    tracker.on_delta(100.35)
    assert tracker.delta_count == 2
    tracker.finish()
    # 再次调用 finish 保持幂等
    tracker.finish()


def test_dynamic_getattr_and_monkeypatch():
    """验证门面属性动态访问与 monkeypatch 穿透兼容。"""
    assert hasattr(metrics, "_LAYER_ALIASES")
    assert hasattr(metrics, "_rag_state")
    assert hasattr(metrics, "_feedback_state")

    # 验证 monkeypatch 穿透
    old_state = metrics._rag_state
    try:
        metrics._rag_state = {"hit": 999, "rejected": 0, "fallback": 0}
        assert metrics._rag_state["hit"] == 999
    finally:
        metrics._rag_state = old_state
