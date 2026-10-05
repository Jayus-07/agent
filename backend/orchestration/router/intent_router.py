"""IntentRouter：路由链中的显式意图分类阶段。

这个模块把原来散落在 ``RuleRouter``、``router_node`` 和层级路由器里的
“用户到底想做什么”判断收口成一个可观测的阶段。它只产出
``IntentDecision``，不选执行节点、不调用 Tool/Skill，也不直接返回最终
``RouteDecision``；统一策略仍由 ``ExecutionModeResolver`` 负责。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.models import (
    DomainDecision,
    IntentDecision,
)
from backend.orchestration.router.types import RouteDecision


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _candidate_names(value: Any) -> list[str]:
    mapping = _as_mapping(value)
    names: list[str] = []
    for candidate in mapping.get("candidates") or []:
        candidate_mapping = _as_mapping(candidate)
        name = str(candidate_mapping.get("name") or "")
        if name:
            names.append(name)
    return names


class IntentRouter:
    """只负责意图分类，不承担执行策略。

    ``RuleRouter`` 仍然保留为规则证据提供者，但不再是主链上的隐式
    “先拍板再补域”入口。任何强规则也必须经过本阶段记录，再交给统一
    策略层形成最终 RouteDecision。
    """

    def classify(
        self,
        query: str,
        domain_decision: DomainDecision,
        rule_decision: RouteDecision | Mapping[str, Any] | None = None,
        *,
        existing_override: Any | None = None,
    ) -> IntentDecision:
        """根据域判断和规则证据产出结构化意图。"""

        del query  # 规则匹配已在 RuleRouter 完成；本阶段只做归一化。
        domain = str(domain_decision.get("domain") or "unknown")
        domain_source = str(domain_decision.get("source") or "")
        subflow = str(domain_decision.get("subflow") or "")

        candidate = rule_decision
        if candidate is None:
            override_mapping = _as_mapping(existing_override)
            if "execution_mode" in override_mapping or "candidates" in override_mapping:
                candidate = existing_override

        candidate_mapping = _as_mapping(candidate)
        names = _candidate_names(candidate)
        candidate_source = (
            "route_engine"
            if rule_decision is None and candidate is not None
            else "rule"
        )
        mode_value = candidate_mapping.get("execution_mode")
        mode = str(getattr(mode_value, "value", mode_value) or "")
        confidence = float(candidate_mapping.get("confidence") or 0.0)
        reason = str(candidate_mapping.get("reason") or "")

        # 预过滤/续跑是有状态域图的入口事实，不能被普通能力规则抢走。
        if domain_source in {"prefilter", "continuation"}:
            if domain == "unknown" or subflow == "clarify":
                return self._decision(
                    intent="clarify",
                    kind="clarify",
                    confidence=float(domain_decision.get("confidence") or 0.0),
                    source=domain_source,
                    reasoning=str(domain_decision.get("reasoning") or "域图入口需要澄清"),
                    execution_hint="clarify",
                )
            if domain == "general":
                return self._decision(
                    intent="general_chat",
                    kind="general",
                    confidence=float(domain_decision.get("confidence") or 0.0),
                    source=domain_source,
                    reasoning="入口域判断为 general，转普通对话",
                    execution_hint="general_chat",
                )
            return self._decision(
                intent=f"{domain}.{subflow}" if subflow else domain,
                kind="domain_graph",
                confidence=float(domain_decision.get("confidence") or 0.0),
                source=domain_source,
                reasoning="入口域图已命中，保留有状态域图语义",
                execution_hint="domain_graph",
            )

        # 强规则是意图分类证据，必须留下清晰的 kind；最终是否短路由策略层
        # 根据同一份证据决定。
        if mode == "workflow":
            return self._decision(
                intent=str(candidate_mapping.get("workflow_name") or (names[0] if names else "workflow")),
                kind="workflow",
                confidence=confidence,
                source=candidate_source,
                reasoning=reason or "规则识别为 workflow 意图",
                execution_hint="workflow",
                candidate_names=names,
            )
        if mode == "plan" and confidence >= 0.8 and len(names) >= 2:
            return self._decision(
                intent="composite",
                kind="composite",
                confidence=confidence,
                source=candidate_source,
                reasoning=reason or "规则识别为复合意图",
                execution_hint="plan",
                candidate_names=names,
            )
        if mode == "direct" and confidence >= 0.8 and names:
            return self._decision(
                intent=names[0],
                kind="single",
                confidence=confidence,
                source=candidate_source,
                reasoning=reason or "规则识别为单能力意图",
                execution_hint="direct",
                candidate_names=names,
            )

        if domain == "general":
            return self._decision(
                intent="general_chat",
                kind="general",
                confidence=float(domain_decision.get("confidence") or 0.0),
                source=domain_source or "domain",
                reasoning="域判断为 general，转普通对话",
                execution_hint="general_chat",
                candidate_names=names,
            )
        if domain == "unknown":
            return self._decision(
                intent="clarify",
                kind="clarify",
                confidence=float(domain_decision.get("confidence") or 0.0),
                source=domain_source or "domain",
                reasoning=str(domain_decision.get("reasoning") or "无法可靠识别用户意图"),
                execution_hint="clarify",
                candidate_names=names,
            )

        # 弱规则只作为候选提示，不能越过能力解析和统一策略直接执行。
        return self._decision(
            intent=names[0] if names else domain,
            kind="task",
            confidence=max(
                float(domain_decision.get("confidence") or 0.0),
                confidence,
            ),
            source="rule_hint" if names else domain_source or "domain",
            reasoning=reason or str(domain_decision.get("reasoning") or "域内任务意图"),
            execution_hint=None,
            candidate_names=names,
        )

    @staticmethod
    def _decision(
        *,
        intent: str,
        kind: str,
        confidence: float,
        source: str,
        reasoning: str,
        execution_hint: str | None,
        candidate_names: list[str] | None = None,
    ) -> IntentDecision:
        return {
            "intent": intent,
            "kind": kind,
            "confidence": max(0.0, min(1.0, confidence)),
            "source": source,
            "reasoning": reasoning,
            "execution_hint": execution_hint,
            "candidate_names": list(candidate_names or []),
        }


__all__ = ["IntentRouter"]
