"""Router 决策到现有 Trace metadata 的最小投影。

只记录低基数、非用户输入的路由结果；不创建 trace、不写 state，也不影响
Router 的路由结果。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from backend.shared.logger import logger


def record_router_decision(
    domain_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    execution_decision: Mapping[str, Any],
    intent_decision: Mapping[str, Any] | None = None,
) -> None:
    """将安全的 Router 决策摘要写入本请求的已有 trace。"""
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is None:
            return

        capability_confidence = _confidence(capability_decision.get("confidence"))
        domain_confidence = _confidence(domain_decision.get("confidence"))
        router_metadata = {
            "domain": _label(domain_decision.get("domain")),
            "subflow": _label(domain_decision.get("subflow")),
            "capability": _label(capability_decision.get("capability")),
            "mode": _label(execution_decision.get("mode")),
            "confidence": (
                capability_confidence
                if capability_confidence is not None
                else domain_confidence
            ),
            "source": _label(
                capability_decision.get("source")
                or domain_decision.get("source")
            ),
        }
        if intent_decision is not None:
            router_metadata.update({
                "intent": _label(intent_decision.get("intent")),
                "intent_kind": _label(intent_decision.get("kind")),
                "intent_source": _label(intent_decision.get("source")),
                "intent_confidence": _confidence(intent_decision.get("confidence")),
            })
        trace.metadata["router"] = router_metadata
    except Exception:
        logger.debug("[RouterTrace] 路由决策 metadata 写入失败", exc_info=True)


def _label(value: Any) -> str | None:
    """将枚举型决策值限制为短字符串，拒绝对象、列表及用户文本。"""
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label[:128] if label else None


def _confidence(value: Any) -> float | None:
    """仅保留有限的数值置信度，避免 NaN/Inf 破坏 JSON 序列化。"""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    return round(confidence, 3)


__all__ = ["record_router_decision"]
