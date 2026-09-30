"""travel/node_span.py — 域图节点 span 埋点（M14 / 治理台账 D14）。

slot_filler / supervisor / repair 三个节点此前无 span——trace 树里这三步
不可见（只有专家/校验/出单有记录），「哪一步耗时、哪一步丢信息」无法归因。
本装饰器给节点包一层 span 生命周期：软失败（无活跃 trace 时 start_span
返回 noop，任何埋点异常不影响节点执行），对齐 validator/reporter 既有口径。

独立文件声明（避免多会话并行改动同一文件，先例 quality_metrics.py）。
span_id 用图节点注册名；同轮多次进入由 tracer P0-2 守卫自动追加 #N 后缀。
kind 沿用 travel 域现状（通用 workflow；SpanKind 无 TRAVEL_* 枚举，不新造
——枚举扩展属 trace 形态变更，另行评审）。
"""
from __future__ import annotations

import functools
from typing import Callable

from backend.shared.logger import logger


def traced_node(span_id: str, display_name: str,
                metrics_fn: Callable[[object], dict] | None = None):
    """图节点 span 装饰器：start_span → fn → end_span（全部软失败）。

    metrics_fn：从节点返回值（update dict 或 LangGraph Command）提取 span
    metrics，提取异常按无 metrics 处理。节点自身异常时 span 以 error 收口
    后原样上抛——埋点绝不改变节点的异常语义。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state):
            try:
                from backend.observability.tracer import trace_collector
                span = trace_collector.start_span(
                    span_id, name=display_name, type="workflow",
                    kind="workflow", input={})
            except Exception:
                logger.debug("[TravelSpan] %s span 开启失败", span_id,
                             exc_info=True)
                span = None
            try:
                update = fn(state)
            except Exception:
                if span is not None:
                    try:
                        trace_collector.end_span(span, status="error",
                                                 metrics={"error": "exception"})
                    except Exception:
                        logger.debug("[TravelSpan] %s span 收口失败", span_id,
                                     exc_info=True)
                raise
            if span is not None:
                try:
                    try:
                        metrics = metrics_fn(update) if metrics_fn else {}
                    except Exception:
                        logger.debug("[TravelSpan] %s metrics 提取失败",
                                     span_id, exc_info=True)
                        metrics = {}
                    trace_collector.end_span(span, metrics=metrics,
                                             status="success")
                except Exception:
                    logger.debug("[TravelSpan] %s span 收口失败", span_id,
                                 exc_info=True)
            return update
        return wrapper
    return deco
