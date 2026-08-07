"""Prometheus 指标 + /metrics 端点 — PR-0.3。

4 个核心 SLI（对齐 docs/architecture/production-readiness.md §1.3）：
- chat_request_total{status}        Counter
- chat_request_duration_seconds     Histogram
- llm_tokens_total{model, direction} Counter
- skill_failure_total{skill, error_type} Counter

业务模块调用方式：
    from backend.observability.metrics import (
        chat_request_total, chat_request_duration_seconds,
        llm_tokens_total, skill_failure_total,
    )
    chat_request_total.labels(status="ok").inc()

HTTP 端点 /metrics 在 server.py 注册。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ==========================================================
# 4 个核心 metric（PR-0.3 最小骨架；后续可加 workflow_run_duration 等）
# ==========================================================

chat_request_total = Counter(
    "chat_request_total",
    "Total chat API requests by final status",
    labelnames=("status",),  # ok | error | rejected | aborted
)

chat_request_duration_seconds = Histogram(
    "chat_request_duration_seconds",
    "Chat request wall-clock duration in seconds",
    buckets=(0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM token usage by model and direction",
    labelnames=("model", "direction"),  # direction: prompt | completion
)

skill_failure_total = Counter(
    "skill_failure_total",
    "Skill execution failures by skill name and error type",
    labelnames=("skill", "error_type"),
)

# ── 流式事件可观测性（P0-1：SSE 队列 backpressure 丢弃计数）──
chat_stream_event_dropped_total = Counter(
    "chat_stream_event_dropped_total",
    "SSE 流式事件被 backpressure / 异常 丢弃的次数",
    labelnames=("reason",),  # queue_full | producer_error
)

chat_stream_event_produced_total = Counter(
    "chat_stream_event_produced_total",
    "SSE 流式事件由 LangGraph 产出的总数（按事件类型）",
    labelnames=("event",),  # status | delta | log | done | error | meta
)


def render_metrics() -> tuple[bytes, str]:
    """生成 Prometheus 文本格式输出。

    Returns:
        (body, content_type) — 给 FastResponse 直接用
    """
    return generate_latest(), CONTENT_TYPE_LATEST


__all__ = [
    "chat_request_total",
    "chat_request_duration_seconds",
    "llm_tokens_total",
    "skill_failure_total",
    "chat_stream_event_dropped_total",
    "chat_stream_event_produced_total",
    "render_metrics",
]
