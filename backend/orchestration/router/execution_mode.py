"""ExecutionModeResolver：把决策结果归一为公开执行方式。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from backend.orchestration.domain_registry import (
    DerivedDomainMap,
    domain_graph_registry,
)
from backend.orchestration.router.manifest import load_manifest
from backend.orchestration.router.models import (
    CapabilityDecision,
    DomainDecision,
    ExecutionModeDecision,
)


# 顶级域图名 → 自身。**派生，零手写**（原为手写三键字典，与决策层/回写层重复）：
# 只含 domain is None 的顶级域图，子流图不作为顶级域入口单列。
_DEFAULT_DOMAIN_GRAPH_MODES: Mapping[str, str] = DerivedDomainMap(
    domain_graph_registry.route_mode_to_graph_mode,
    label="_DEFAULT_DOMAIN_GRAPH_MODES",
)


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
            _DEFAULT_DOMAIN_GRAPH_MODES
            if domain_graph_modes is None
            else domain_graph_modes
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
        # 子流选图：查注册表归属（取代原 f"travel_{subflow}" 字符串拼接）。
        # 拼接把「谁是 travel 的子流」编码进命名约定——注册表新增子流而漏改
        # 拼接时，会**静默**落回顶级域图（不报错、办错事）。此处改为查表：
        # 查不到就按顶级域兜底，行为与拼接期等价（travel+planning 仍落 travel）。
        subflow_graph = domain_graph_registry.find_subflow_graph(
            domain, decision.get("subflow"),
        )
        if subflow_graph is not None:
            return subflow_graph
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
