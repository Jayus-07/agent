"""context_budget.metrics — 上下文预算指标 + SSE context 事件出口

低基数约束（规格 §十四）：Prometheus label 只含 level/action/stage，
禁止 session_id/user_id/turn_id 进 label；这些 ID 只进结构化日志/Trace。

SSE context 事件采用与 infra.llm.proxy 的 stream_sink 相同的模式：
ContextVar 挂一个 sink callable，由 GraphRunner 每轮请求设置，Guard /
MicroCompactor 通过 emit_context_event 发声（sink 未挂载时静默跳过——
后台任务 / 测试路径无流式通道，属正常场景）。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Callable

from backend.shared.logger import logger

# ---------------------------------------------------------------------------
# Prometheus 指标（低基数）
# ---------------------------------------------------------------------------

# 指标定义统一收口在 observability/metrics.py（规格 §十四），此处仅引用；
# prometheus_client 不可用时软降级为 None（调用侧已判空）。
try:
    from backend.observability.metrics import (
        context_budget_overflow_total,
        context_compactions_total,
        context_tokens_saved_total,
    )
except Exception:  # pragma: no cover — observability 层不可用时软降级
    context_compactions_total = None
    context_tokens_saved_total = None
    context_budget_overflow_total = None


def record_compaction(
    *, level: str, action: str, before_tokens: int, after_tokens: int
) -> None:
    """记录一次压缩：metric + 结构化日志（不发声 SSE，见 emit_context_event）。"""
    saved = max(0, before_tokens - after_tokens)
    try:
        if context_compactions_total is not None:
            context_compactions_total.labels(level=level, action=action).inc()
        if context_tokens_saved_total is not None:
            context_tokens_saved_total.labels(level=level).inc(saved)
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


def record_overflow(stage: str) -> None:
    """记录一次预算溢出（全量裁剪后仍超限）。"""
    try:
        if context_budget_overflow_total is not None:
            context_budget_overflow_total.labels(stage=stage).inc()
    except Exception:
        logger.debug("context metric 记录失败", exc_info=True)


# ---------------------------------------------------------------------------
# SSE context 事件出口（ContextVar sink，模式与 stream_sink 一致）
# ---------------------------------------------------------------------------

# data 形如 {"type": "context", "level": "L1", "action": "tool_compact", ...}
ContextSink = Callable[[dict], None]
_context_sink: ContextVar[ContextSink | None] = ContextVar(
    "context_event_sink", default=None
)


def set_context_sink(sink: ContextSink) -> object:
    """挂载本轮请求的 context 事件 sink，返回 token 供 reset 使用。"""
    return _context_sink.set(sink)


def reset_context_sink(token: object) -> None:
    try:
        _context_sink.reset(token)  # type: ignore[arg-type]
    except Exception:
        _context_sink.set(None)


def emit_context_event(
    *,
    level: str,
    action: str,
    before_tokens: int,
    after_tokens: int,
    **extra: Any,
) -> None:
    """发一条 context SSE 事件（sink 未挂载时跳过；绝不携带完整工具结果）。

    格式（规格 §十一）：
      {"type": "context", "level": "L1", "action": "tool_compact",
       "before_tokens": 5200, "after_tokens": 250, "saved_tokens": 4950}
    """
    saved = max(0, before_tokens - after_tokens)
    data = {
        "type": "context",
        "level": level,
        "action": action,
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": saved,
        "ts": time.time(),
        **extra,
    }
    sink = _context_sink.get()
    if sink is None:
        return
    try:
        sink(data)
    except Exception:
        logger.debug("context SSE 事件发送失败", exc_info=True)
