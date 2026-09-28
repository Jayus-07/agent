"""ExecutionModeResolver：把决策结果归一为公开执行方式。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from backend.orchestration.router.manifest import load_manifest
from backend.orchestration.router.models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
)


_DEFAULT_DOMAIN_GRAPH_MODES = {
    "customer_service": "customer_service",
    "selection_funnel": "selection_funnel",
    "travel": "travel",
}


class ExecutionModeResolver:
    """纯规则 resolver；不执行 Tool、Skill、Workflow 或域图。"""

    def __init__(
        self,
        workflow_names: Iterable[str] | None = None,
        domain_graph_modes: Mapping[str, str] | None = None,
    ):
        self._workflow_names = set(
            workflow_names
            if workflow_names is not None
            else (workflow.name for workflow in load_manifest().workflows)
        )
        self._domain_graph_modes = dict(
            domain_graph_modes or _DEFAULT_DOMAIN_GRAPH_MODES,
        )

    def resolve(
        self,
        domain_decision: DomainDecision,
        capability_decision: CapabilityDecision,
        existing_override: Any | None = None,
    ) -> ExecutionModeDecision:
        """按兼容优先级返回统一执行方式。"""

        override = self._as_mapping(existing_override)
        compat_route_mode = str(override.get("route_mode") or "")
        if compat_route_mode == "clarify":
            return ExecutionModeDecision(
                mode="plan",
                target="clarify",
                confidence=float(domain_decision.get("confidence") or 0.0),
                reasoning="保留旧 clarify route_mode，交兼容 reporter 短路",
                compat_route_mode="clarify",
            )

        override_mode_value = override.get("execution_mode") or ""
        override_mode = str(
            getattr(override_mode_value, "value", override_mode_value)
        )
        if override_mode == "workflow":
            target = str(
                override.get("workflow_name")
                or self._first_candidate(override)
                or ""
            )
            if target and target not in self._workflow_names:
                target = ""
            return ExecutionModeDecision(
                mode="workflow",
                target=target or None,
                confidence=float(override.get("confidence") or 0.0),
                reasoning="复用既有 workflow override",
            )
        if override_mode == "direct":
            target = self._first_candidate(override)
            return ExecutionModeDecision(
                mode="direct",
                target=target or capability_decision.get("capability"),
                confidence=float(override.get("confidence") or 0.0),
                reasoning="复用既有 direct override",
            )
        if override_mode == "plan":
            return ExecutionModeDecision(
                mode="plan",
                confidence=float(override.get("confidence") or 0.0),
                reasoning="复用既有 plan override",
            )

        domain = str(domain_decision.get("domain") or "unknown")
        confidence = float(domain_decision.get("confidence") or 0.0)
        if domain == "general":
            return ExecutionModeDecision(
                mode="general",
                confidence=confidence,
                reasoning="general 域无 Tool，保持直接回答",
            )

        graph_target = self._domain_graph_target(domain_decision)
        if graph_target is not None:
            return ExecutionModeDecision(
                mode="domain_graph",
                target=graph_target,
                confidence=confidence,
                reasoning="prefilter/continuation 命中有状态域图",
            )

        capability = capability_decision.get("capability")
        if capability:
            return ExecutionModeDecision(
                mode="direct",
                target=capability,
                confidence=float(capability_decision.get("confidence") or 0.0),
                reasoning="域内已有 top1 capability 候选",
            )

        if capability_decision.get("candidates"):
            return ExecutionModeDecision(
                mode="plan",
                confidence=float(capability_decision.get("confidence") or 0.0),
                reasoning="域内存在多个候选，交 Planner 形成 DAG",
            )

        return ExecutionModeDecision(
            mode="plan",
            confidence=confidence,
            reasoning="无可执行候选，保持旧 plan fallback",
        )

    def _domain_graph_target(self, decision: DomainDecision) -> str | None:
        if decision.get("source") not in {"prefilter", "continuation"}:
            return None
        domain = str(decision.get("domain") or "")
        subflow = decision.get("subflow")
        if domain == "travel" and subflow in {"booking", "commerce"}:
            return f"travel_{subflow}"
        return self._domain_graph_modes.get(domain)

    @staticmethod
    def _as_mapping(value: Any | None) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            mode = dumped.get("execution_mode")
            if hasattr(mode, "value"):
                dumped["execution_mode"] = mode.value
            return dumped if isinstance(dumped, Mapping) else {}
        return {}

    @staticmethod
    def _first_candidate(value: Mapping[str, Any]) -> str | None:
        candidates = value.get("candidates") or []
        if not candidates:
            return None
        first = candidates[0]
        if isinstance(first, Mapping):
            return str(first.get("name") or "") or None
        return str(first) or None


__all__ = ["ExecutionModeResolver"]
