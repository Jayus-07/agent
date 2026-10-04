"""客服图节点 Trace 辅助。

客服图的四个编排节点返回值并不都符合主图 ``TraceMiddleware`` 的 dict
约束（待处理器与调度器返回 LangGraph ``Command``），因此这里使用独立的
轻量包装器。专家节点继续由 ``CsExpertHooks`` 负责，避免重复产生 Span。
"""
from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

from backend.observability.tracer import SpanKind, trace_collector

_NODE_LABELS = {
    "cs_state_loader": "客服状态加载",
    "cs_pending_handler": "客服待处理",
    "cs_supervisor": "客服调度",
    "cs_reporter": "客服汇总",
}

_NODE_KINDS = {
    "cs_state_loader": SpanKind.CS_STATE_LOADER.value,
    "cs_pending_handler": SpanKind.CS_CONFIRMATION.value,
    "cs_supervisor": SpanKind.CS_SUPERVISOR.value,
    "cs_reporter": SpanKind.CS_REPORTER.value,
}


def _enum_value(value: Any) -> str:
    """把路由枚举或字符串转换为稳定的低基数字段。"""
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw or "")


def _route_fields(state: dict[str, Any], node_name: str) -> dict[str, str]:
    """提取客服节点公共字段，不把用户消息或业务实体写进 Span。"""
    route = state.get("cs_route") or {}
    if not isinstance(route, dict):
        route = {}
    return {
        "domain": "customer_service",
        "node": node_name,
        "intent": _enum_value(route.get("intent")),
        "route_path": _enum_value(route.get("route_path")),
    }


def _summarize_output(result: Any) -> dict[str, Any]:
    """仅保留编排结果，明确排除 final_answer、user_message 等内容。"""
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}

    summary: dict[str, Any] = {}
    decision = result.get("supervisor_decision")
    if isinstance(decision, dict):
        if decision.get("next_action"):
            summary["decision_action"] = _enum_value(decision["next_action"])
        if decision.get("next_expert"):
            summary["decision_expert"] = _enum_value(decision["next_expert"])
    for key in ("current_expert", "handoff_state", "confirmation_state"):
        value = result.get(key)
        if value:
            summary[key] = _enum_value(value)
    return summary


def wrap_cs_node(
    node_name: str,
    node_fn: Callable[[dict[str, Any]], Any],
) -> Callable[[dict[str, Any]], Any]:
    """为客服图核心节点添加统一 Span，保持节点返回值完全不变。"""
    label = _NODE_LABELS.get(node_name, node_name)
    kind = _NODE_KINDS.get(node_name, SpanKind.AGENT.value)

    @functools.wraps(node_fn)
    def wrapper(state: dict[str, Any]):
        fields = _route_fields(state, node_name)
        span = trace_collector.start_span(
            f"cs_node:{node_name}",
            name=label,
            type=SpanKind.AGENT.value,
            kind=kind,
            input=fields,
        )
        started = time.monotonic()
        try:
            result = node_fn(state)
            elapsed_ms = (time.monotonic() - started) * 1000
            trace_collector.end_span(
                span,
                output=_summarize_output(result),
                metrics={
                    **fields,
                    "elapsed_ms": round(elapsed_ms, 1),
                },
                status="success",
            )
            return result
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            trace_collector.end_span(
                span,
                metrics={
                    **fields,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "error_type": type(exc).__name__,
                },
                status="error",
            )
            raise

    return wrapper


__all__ = ["wrap_cs_node"]
