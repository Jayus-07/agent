"""ExecutionModeResolver：把决策结果归一为公开执行方式。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
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
        *,
        verified_context: Mapping[str, Any] | None = None,
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
                return self._clarify(
                    float(override.get("confidence") or 0.0),
                    "workflow_not_registered",
                )
            if not target:
                return self._clarify(
                    float(override.get("confidence") or 0.0),
                    "workflow_target_missing",
                )
            return ExecutionModeDecision(
                mode="workflow",
                target=target,
                confidence=float(override.get("confidence") or 0.0),
                reasoning="复用既有 workflow override",
            )
        # direct override 只是旧入口的路由提示，不是执行授权；继续走下方
        # 同一套能力、分数、margin、风险、注册和权限门禁。
        if override_mode == "plan":
            if self._valid_composite_override(override, verified_context):
                return ExecutionModeDecision(
                    mode="plan",
                    confidence=float(override.get("confidence") or 0.0),
                    reasoning="复用已校验的复合意图 plan override",
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

        selection_mode = str(capability_decision.get("selection_mode") or "")
        candidate_rows = capability_decision.get("candidates") or []
        candidates = {
            str(item.get("name") or ""): item
            for item in candidate_rows
            if isinstance(item, Mapping) and item.get("name")
        }
        top1 = str(capability_decision.get("top1") or capability_decision.get("capability") or "")
        if selection_mode == "fast_path":
            reason = self._fast_path_block_reason(
                domain, top1, capability_decision, candidates.get(top1),
            )
            if not reason:
                return ExecutionModeDecision(
                    mode="direct",
                    target=top1,
                    confidence=float(capability_decision.get("top1_score") or 0.0),
                    reasoning="Fast Path 满足分数、margin、注册、白名单、风险与权限门禁",
                )
            if not candidates:
                return self._clarify(float(capability_decision.get("confidence") or confidence), reason)
            # 可信分数/分差不满足时仍可进入既有 Tool Selector 消歧；target
            # 必须为空，禁止 selector 失败后静默执行首候选。
            if any(not row.get("permission_ready", True) for row in candidates.values()):
                return self._clarify(float(capability_decision.get("confidence") or confidence), "permission_denied")
            return ExecutionModeDecision(
                mode="direct",
                target=None,
                confidence=0.0,
                reasoning=f"Fast Path 被阻断（{reason}），交 Tool Selector 消歧",
            )

        if selection_mode == "llm_selection" and candidates:
            if any(not row.get("permission_ready", True) for row in candidates.values()):
                return self._clarify(float(capability_decision.get("confidence") or confidence), "permission_denied")
            return ExecutionModeDecision(
                mode="direct",
                target=None,
                confidence=0.0,
                reasoning="灰区候选交现有 Tool Selector 消歧，未指定执行目标",
            )

        return self._clarify(
            float(capability_decision.get("confidence") or confidence),
            str(capability_decision.get("block_reason") or "no_safe_candidate"),
        )

    def _fast_path_block_reason(
        self,
        domain: str,
        capability: str,
        decision: CapabilityDecision,
        row: Mapping[str, Any] | None,
    ) -> str:
        if not capability or row is None:
            return "top1_not_in_registered_candidates"
        if not row.get("permission_ready", False):
            return "permission_denied"
        if str(decision.get("selection_mode") or "") != "fast_path":
            return "selection_mode_not_fast_path"

        try:
            from backend.orchestration.capability_registry import tool_registry
            from backend.core.tool_governance.registry import get_tool_spec
            canonical_domain = {"travel_booking": "travel", "travel_commerce": "travel"}.get(domain, domain)
            declaration = next(
                item for item in load_manifest().capabilities if item.name == capability
            )
            spec = get_tool_spec(capability)
            declared_domains = set(spec.domains if spec is not None else ())
            declared_domain = str(getattr(declaration, "domain", "") or "")
            if not declaration.routed or (
                canonical_domain != declared_domain and canonical_domain not in declared_domains
            ) or tool_registry.get_node(capability) is None:
                return "capability_not_registered_for_domain"
            if not declaration.fast_path_enabled or not row.get("fast_path_enabled", False):
                return "fast_path_not_allowlisted"
            if declaration.risk_level.upper() != "LOW" or str(row.get("risk") or "UNKNOWN").upper() != "LOW":
                return "risk_level_not_low"
        except Exception:
            return "capability_registry_validation_failed"

        try:
            from backend.config import FINE_TOOL_HIGH_CONFIDENCE, FINE_TOOL_MIN_MARGIN
            score = float(decision.get("top1_score"))
            row_score = float(row.get("score"))
            margin = float(decision.get("margin"))
            top2_name = str(decision.get("top2") or "")
            top2_score = float(decision.get("top2_score") or 0.0)
            if not all(isfinite(value) for value in (score, row_score, margin, top2_score)):
                return "non_finite_score_metadata"
            if top2_name:
                top2_row = next(
                    (item for item in decision.get("candidates") or []
                     if isinstance(item, Mapping) and item.get("name") == top2_name),
                    None,
                )
                if top2_row is None or abs(float(top2_row.get("score")) - top2_score) > 0.001:
                    return "top2_score_metadata_mismatch"
            elif top2_score != 0.0:
                return "top2_candidate_missing"
            if abs(row_score - score) > 0.001 or abs((score - top2_score) - margin) > 0.001:
                return "score_margin_metadata_mismatch"
            score_type = str(decision.get("score_type") or "")
            if score_type not in {
                "vector_similarity_heuristic",
                "adjusted_vector_similarity_heuristic",
            }:
                return "score_type_not_fast_path_eligible"
        except (TypeError, ValueError):
            return "invalid_score_metadata"
        if score < FINE_TOOL_HIGH_CONFIDENCE:
            return "top1_score_below_threshold"
        if margin < FINE_TOOL_MIN_MARGIN:
            return "margin_below_threshold"
        return ""

    @staticmethod
    def _valid_composite_override(
        override: Mapping[str, Any],
        verified_context: Mapping[str, Any] | None = None,
    ) -> bool:
        if float(override.get("confidence") or 0.0) < 0.8:
            return False
        candidates = override.get("candidates") or []
        if len(candidates) < 2:
            return False
        try:
            from backend.orchestration.capability_registry import tool_registry
            from backend.orchestration.router.capability_router import CapabilityRouter
            allowed = set(load_manifest().planner_visible_capability_names)
            for item in candidates:
                name = str(item.get("name") if isinstance(item, Mapping) else item)
                if name not in allowed or tool_registry.get_node(name) is None:
                    return False
                if not CapabilityRouter._permission_ready(name, dict(verified_context or {})):
                    return False
        except Exception:
            return False
        return True

    @staticmethod
    def _clarify(confidence: float, reason: str) -> ExecutionModeDecision:
        return ExecutionModeDecision(
            mode="plan",
            target="clarify",
            confidence=confidence,
            reasoning=f"安全路由阻断（{reason}），请求澄清",
            compat_route_mode="clarify",
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
