# -*- coding: utf-8 -*-
"""Tool Span 生命周期适配层。

Tool 的真实执行入口不止 BaseSkill：旅游直调、工作流适配器和治理探针都
需要写出同一套 ``tool_call`` Span。这里把公共字段和错误分类收敛到一处，
调用方已有 Span 时则由调用方负责结束，避免重复记录。
"""
from __future__ import annotations

from typing import Any

from backend.core.tool_runtime.models import ToolResult, ToolStatus
from backend.observability.error_taxonomy import unify_tool_status
from backend.observability.tracer import Span, SpanKind, trace_collector


def start_tool_span(
    tool_name: str,
    capability: str = "",
    params: dict[str, Any] | None = None,
    agent: str = "",
) -> Span:
    """创建统一 Tool Span；没有 active Trace 时返回 tracer 的 noop Span。"""
    canonical_tool = tool_name or capability or "unknown_tool"
    return trace_collector.start_span(
        f"tool:{canonical_tool}",
        name=canonical_tool,
        type="tool_call",
        kind=SpanKind.TOOL.value,
        input={
            "tool": canonical_tool,
            "capability": capability,
            "agent": agent,
            "params": params or {},
        },
    )


def finish_tool_span(
    span: Span,
    result: ToolResult,
    output: Any = None,
) -> None:
    """按 ToolResult 收口 Span，并写入统一错误分类与失败事件。"""
    error_class = unify_tool_status(result.status)
    metrics: dict[str, Any] = {
        "latency_ms": result.latency_ms,
        "retries": result.retry_count,
        "fallback_used": result.fallback_used or "",
    }
    if result.status is ToolStatus.SUCCESS:
        metrics["error_class"] = "success"
        trace_collector.end_span(
            span,
            output={"result": output if output is not None else result.data},
            metrics=metrics,
            status="success",
        )
        return

    metrics.update({
        "error_class": error_class,
        "error_code": result.error_code or result.status.value,
        "error": result.error_message or result.user_friendly_message(),
    })
    trace_collector.add_event(
        span,
        "tool.error",
        "error",
        metrics["error"],
        {
            "error_class": error_class,
            "error_code": metrics["error_code"],
        },
    )
    trace_collector.end_span(span, metrics=metrics, status="error")


__all__ = ["start_tool_span", "finish_tool_span"]
