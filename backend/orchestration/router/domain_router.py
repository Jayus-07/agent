"""DomainRouter：把既有域入口结果归一为 DomainDecision。

域 prefilter 仍由 ``router_node`` 按既有顺序执行。本适配器只负责把命中结果
转换成统一结构；没有 prefilter 结果时才调用既有粗分类器。这样可以保留客服
锁域、灰度、旅游/选品优先级等入口语义，同时避免在 Router 层复制正则算法。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.domain_registry import (
    DerivedDomainMap,
    domain_graph_registry,
)
from backend.orchestration.router.domain_classifier import (
    CoarseIntentClassifier,
    get_coarse_classifier,
)
from backend.orchestration.router.models import DomainDecision


# 伪路由模式：有 route_mode 但**没有域图**——寒暄/能力咨询走 general_chat 直答
# （general 无 Tool、无域图节点），注册表装不下，故这里是全表唯一的手写项。
#
# 为什么不能派生掉：general_chat 的归宿是**主图内置节点**（router_node.route_selector
# 的 "general_chat" 分支 → builder 的 general_chat 节点），注册表只装「独立域子图」。
# 硬给它造一张假域图会①让 builder 重复布线；②把它写进回写层活跃域——一句「你好」
# 就冲掉进行中的跨轮任务上下文。所以这是**结构性例外**，不是手写偷懒。
# 企业做法=显式例外 + 可证最小 + 可证有归宿：
#   ①不得与注册表相交（例外表后置合并，会覆盖派生值）
#   ②每个键必须真有主图归宿（route_selector 不得落到 planner）
#   ③不得进回写层/执行层
# 三条均由 tests/orchestration/test_domain_semantic_consistency.py 守护。
_NON_GRAPH_ROUTE_MODES: dict[str, tuple[str, str | None]] = {
    "general_chat": ("general", None),
}


def _derive_prefilter_domain_map() -> dict[str, tuple[str, str | None]]:
    """域图部分全部由注册表派生（顶级域看 graph.domain，subflow 看展示标签）。"""
    return {
        **domain_graph_registry.route_mode_to_domain_decision(),
        **_NON_GRAPH_ROUTE_MODES,
    }


# route_mode → (顶级域, subflow)。**派生，零手写**：内容 = 注册表每个域图
# 的归属归一 + 上面的无图伪模式。原为手写字典，与注册表/回写层三处各写一份，
# 漏改任一处都是静默错误的入口（详见 domain_registry 顶部说明）。
_PREFILTER_DOMAIN_MAP: Mapping[str, tuple[str, str | None]] = DerivedDomainMap(
    _derive_prefilter_domain_map,
    label="_PREFILTER_DOMAIN_MAP",
)


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
            "margin": float(getattr(prediction, "margin", 0.0)),
            "second_domain": str(getattr(prediction, "second_domain", "") or ""),
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
               str(meta.get("domain_source") or "route_engine"))
        return {
            "domain": domain,
            "subflow": None,
            "confidence": float(meta.get("domain_confidence") or 0.0),
            "margin": float(meta.get("domain_margin") or 0.0),
            "second_domain": str(meta.get("domain_second") or ""),
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
