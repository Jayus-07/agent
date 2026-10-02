"""travel/core/events.py — 旅游域统一事件出口（v3 §2 骨架）

Phase 1 仅提供结构化 logger 出口；quality_metrics 的 Prometheus 面在
Phase 3 统一收编（届时各专家散落的 qm.record_* 改经此处）。事件字段一律
JSON 可序列化（default=str 兜底），禁止把运行时对象塞进日志上下文。
"""
from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from backend.shared.logger import logger

EVENT_SOURCE = "travel"
_EventSink = Callable[[dict[str, Any]], None]
_ResultSummary = Callable[[Any], dict[str, Any]]
_event_sink: contextvars.ContextVar[_EventSink | None] = contextvars.ContextVar(
    "travel_event_sink", default=None,
)
_ResultT = TypeVar("_ResultT")


def build_travel_event(event: str, agent: str = "", **fields: Any) -> dict:
    """构造事件 dict（纯函数，可单测）。event/agent 为必填语义字段。"""
    # provider/source 是 Tool 结果的业务字段，不能覆盖事件总线的来源标识。
    fields.pop("source", None)
    return {"source": EVENT_SOURCE, "event": event, "agent": agent, **fields}


def emit_travel_event(event: str, agent: str = "", **fields: Any) -> dict:
    """构造、落日志并投递到当前请求的 SSE sink。

    sink 是旁路出口：客户端断开或队列已关闭时，事件投递失败不能改变
    旅游域本身的执行结果。业务结果仍只由真实 Service/Provider 决定。
    """
    payload = build_travel_event(event, agent=agent, **fields)
    logger.info("travel_event: %s", json.dumps(payload, ensure_ascii=False, default=str))
    sink = _event_sink.get()
    if sink is not None:
        try:
            sink(payload)
        except Exception:  # noqa: BLE001 — SSE 断开不改变业务执行语义
            logger.debug("[TravelEvent] sink 投递失败", exc_info=True)
    return payload


@contextmanager
def travel_event_scope(sink: _EventSink) -> Iterator[None]:
    """把事件 sink 绑定到当前图执行上下文，退出时恢复旧值。"""
    token = _event_sink.set(sink)
    try:
        yield
    finally:
        _event_sink.reset(token)


def run_travel_tool(
    tool: str,
    agent: str,
    fn: Callable[[], _ResultT],
    *,
    result_summary: _ResultSummary | None = None,
) -> _ResultT:
    """执行真实旅游 Tool，并发出开始/结果事件。

    这里不兜底、不补造结果：调用方的 Tool 异常会原样重新抛出，
    ``run_expert_safely`` 再按旅游域既有失败契约收口为 failed。返回空结果
    仍然是成功调用，是否为空由 result_summary 明确标记，不能混成失败。
    """
    started_at = time.monotonic()
    emit_travel_event("tool.started", agent=agent, tool=tool)
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 — 先发事件，再保持原失败语义
        emit_travel_event(
            "tool.result",
            agent=agent,
            tool=tool,
            status="failed",
            error_type=type(exc).__name__,
            error=str(exc),
            data_status="unavailable",
            duration_ms=round((time.monotonic() - started_at) * 1000),
        )
        raise

    fields: dict[str, Any] = {
        "status": "success",
        "duration_ms": round((time.monotonic() - started_at) * 1000),
    }
    if result_summary is not None:
        try:
            fields.update(result_summary(result))
        except Exception:  # noqa: BLE001 — 摘要失败不改变真实 Tool 结果
            logger.debug("[TravelEvent] Tool 结果摘要失败: %s", tool,
                         exc_info=True)
            fields["summary_status"] = "unavailable"
    emit_travel_event("tool.result", agent=agent, tool=tool, **fields)
    return result
