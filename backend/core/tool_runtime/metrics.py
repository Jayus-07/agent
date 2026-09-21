"""tool_runtime/metrics.py — Tool 治理 Prometheus 指标（桥接 observability.metrics）

Label 基数控制：只含 tool / domain / status（status 取 ToolStatus 值），
禁止 user_id / request_id / session_id / error_message 进 label。
所有上报 soft-fail：指标失败不影响业务执行。
"""
from __future__ import annotations

from backend.core.tool_runtime.models import ToolResult, ToolStatus


def _record(metric_name: str, labels: dict, value: float = 1) -> None:
    try:
        from backend.observability import metrics as m
        counter = getattr(m, metric_name, None)
        if counter is None:
            return
        counter.labels(**labels).inc(value)
    except Exception:  # pragma: no cover — 指标软失败
        pass


def record_tool_result(result: ToolResult, domain: str) -> None:
    labels = {"tool": result.tool_name, "domain": domain, "status": result.status.value}
    _record("agent_tool_calls_total", labels)
    if result.status is ToolStatus.TIMEOUT:
        _record("agent_tool_timeout_total", {"tool": result.tool_name, "domain": domain})
    elif result.status is ToolStatus.RATE_LIMITED or result.status is ToolStatus.FAILED or result.status is ToolStatus.UNAVAILABLE:
        _record("agent_tool_failure_total", {"tool": result.tool_name, "domain": domain})
    if result.retry_count:
        _record("agent_tool_retry_total", {"tool": result.tool_name, "domain": domain},
                value=result.retry_count)
    if result.fallback_used:
        _record("agent_tool_fallback_total",
                {"tool": result.tool_name, "domain": domain, "reason": result.fallback_used})
    # 延迟直方图
    try:
        from backend.observability import metrics as m
        hist = getattr(m, "agent_tool_latency_seconds", None)
        if hist is not None:
            hist.labels(tool=result.tool_name).observe(min(result.latency_ms, 120_000) / 1000)
    except Exception:  # pragma: no cover
        pass


def record_circuit_open(tool: str) -> None:
    _record("agent_tool_circuit_open_total", {"tool": tool})


def record_request_degraded(domain: str = "workflow") -> None:
    _record("agent_request_degraded_total", {"domain": domain})
