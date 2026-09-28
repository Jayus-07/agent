"""DomainRouter：把既有域入口结果归一为 DomainDecision。

域 prefilter 仍由 ``router_node`` 按既有顺序执行。本适配器只负责把命中结果
转换成统一结构；没有 prefilter 结果时才调用既有粗分类器。这样可以保留客服
锁域、灰度、旅游/选品优先级等入口语义，同时避免在 Router 层复制正则算法。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.domain_classifier import (
    CoarseIntentClassifier,
    get_coarse_classifier,
)
from backend.orchestration.router.models import DomainDecision


_PREFILTER_DOMAIN_MAP: dict[str, tuple[str, str | None]] = {
    "customer_service": ("customer_service", None),
    "travel": ("travel", "planning"),
    "selection_funnel": ("selection_funnel", "funnel"),
    "travel_booking": ("travel", "booking"),
    "travel_commerce": ("travel", "commerce"),
    "general_chat": ("general", None),
}


class DomainRouter:
    """只判断顶级域，不选择 capability，也不执行任何业务操作。"""

    def __init__(self, classifier: CoarseIntentClassifier | None = None):
        self._classifier = classifier or get_coarse_classifier()

    def route(
        self,
        query: str,
        state: Mapping[str, Any] | None = None,
        *,
        prefilter_update: Mapping[str, Any] | None = None,
    ) -> DomainDecision:
        """返回统一域判断。

        ``prefilter_update`` 是显式参数，避免适配器隐式重跑有副作用的域门禁。
        为便于主图接线，也兼容从 state 的内部适配键读取；该键不会写回主状态。
        """

        state = state or {}
        update = prefilter_update
        if update is None:
            candidate = state.get("_domain_prefilter_update")
            if isinstance(candidate, Mapping):
                update = candidate

        if isinstance(update, Mapping):
            decision = self._from_prefilter(update)
            if decision is not None:
                return decision

        domain_hint = str(state.get("domain_hint") or "").strip().lower()
        if domain_hint in {"customer_service", "cs"}:
            return {
                "domain": "customer_service",
                "subflow": None,
                "confidence": 1.0,
                "source": "prefilter",
                "reasoning": "domain_hint customer_service 锁域",
            }

        context = state.get("routing_context")
        prediction = self._classifier.classify(
            query,
            context if isinstance(context, Mapping) else None,
        )
        return self.from_prediction(prediction)

    @staticmethod
    def from_prediction(prediction: Any) -> DomainDecision:
        """把既有粗分类结果转换为新契约，不重新执行分类。"""

        source = {
            "rule": "rule",
            "classifier": "embedding",
            "gate": "embedding",
            "degraded": "fallback",
        }.get(prediction.source, prediction.source)
        return {
            "domain": prediction.domain,
            "subflow": None,
            "confidence": float(prediction.confidence),
            "source": source,
            "reasoning": prediction.reason_code,
        }

    @staticmethod
    def from_hierarchical_meta(meta: Mapping[str, Any]) -> DomainDecision:
        """从既有 ``routing_meta`` 派生 DomainDecision，避免二次 embedding。"""

        domain = str(meta.get("domain") or "unknown")
        source = {
            "rule": "rule",
            "classifier": "embedding",
            "gate": "embedding",
            "degraded": "fallback",
        }.get(str(meta.get("domain_source") or ""),
               str(meta.get("domain_source") or "legacy"))
        return {
            "domain": domain,
            "subflow": None,
            "confidence": float(meta.get("domain_confidence") or 0.0),
            "source": source,
            "reasoning": str(meta.get("reason_code") or "hierarchical"),
        }

    @staticmethod
    def _from_prefilter(update: Mapping[str, Any]) -> DomainDecision | None:
        route_mode = str(update.get("route_mode") or "")
        mapped = _PREFILTER_DOMAIN_MAP.get(route_mode)
        if mapped is None and route_mode == "clarify":
            # 客服 InputGuard 的 clarify 更新不带 cs_context，仍需保留客服域语义。
            if (
                "cs_context" in update
                or "final_answer" in update
            ):
                mapped = ("customer_service", "clarify")
            elif "_clarify" in update:
                mapped = ("unknown", "clarify")
        if mapped is None:
            return None
        domain, subflow = mapped
        return {
            "domain": domain,
            "subflow": subflow,
            "confidence": 1.0,
            "source": "prefilter",
            "reasoning": f"prefilter 命中 route_mode={route_mode}",
        }


__all__ = ["DomainRouter"]
