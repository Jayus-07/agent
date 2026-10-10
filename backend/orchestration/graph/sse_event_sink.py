"""请求级主图 SSE 事件转发器。

通过 ContextVar 把当前 Runner 的事件队列安全地传给同步/异步 Skill；
阶段事件复用现有 SSE status/log 帧，不改变事件协议。
"""
from __future__ import annotations

import contextvars
import time
from collections.abc import Callable
from typing import Any


SSEEventSink = Callable[[dict[str, Any]], None]

_sink_var: contextvars.ContextVar[SSEEventSink | None] = contextvars.ContextVar(
    "graph_sse_event_sink", default=None,
)


def bind_sse_event_sink(sink: SSEEventSink) -> contextvars.Token:
    """把本轮事件接收器绑定到当前执行上下文。"""
    return _sink_var.set(sink)


def reset_sse_event_sink(token: contextvars.Token) -> None:
    """恢复绑定前的接收器，避免请求间串流。"""
    _sink_var.reset(token)


def emit_sse_event(event: dict[str, Any]) -> None:
    """转发已有 SSE 事件；观测链路失败不影响业务执行。"""
    sink = _sink_var.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        return


def emit_sse_progress(
    *,
    node: str,
    phase: str,
    message: str,
    tool: str = "",
) -> None:
    """发送单条安全、结构化的进度 log 帧。"""
    payload: dict[str, Any] = {"phase": phase, "status": "running"}
    if tool:
        payload["tool"] = tool
    emit_sse_event({
        "event": "log",
        "data": {
            "level": "info",
            "node": node,
            "step_id": phase,
            "message": message,
            "payload": payload,
            "ts": time.time(),
        },
    })
