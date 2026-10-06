"""RouteDecisionV2 到旧主图状态的唯一兼容投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.types import (
    CapabilityDecisionV2,
    DomainDecisionV2,
    ExecutionDecision,
    ExecutionMode,
    InteractionDecision,
    InteractionMode,
    IntentDecisionV2,
    RouteDecision,
    RouteDecisionV2,
    RuntimeTarget,
    RuntimeType,
)
from backend.orchestration.domain_registry import domain_graph_registry


def build_route_decision_v2_from_route(
    route_mode: str,
    *,
    domain: str | None = None,
    subflow: str | None = None,
    workflow_name: str | None = None,
    confidence: float = 0.0,
    reason: str = "兼容路由归一",
) -> RouteDecisionV2:
    """把现有 route_mode 归一为 V2，不执行任何业务逻辑。"""

    route_mode = str(route_mode or "plan")
    domain_name = domain or "general"
    interaction = InteractionMode.EXECUTE
    execution = ExecutionMode.PLAN
    runtime_type = RuntimeType.PLAN
    runtime_id = "capability_plan"

    if route_mode == "direct":
        execution = ExecutionMode.DIRECT
        runtime_type = RuntimeType.DIRECT
        runtime_id = "direct"
    elif route_mode == "workflow":
        execution = ExecutionMode.WORKFLOW
        runtime_type = RuntimeType.WORKFLOW
        runtime_id = workflow_name or "workflow"
    elif route_mode == "general_chat":
        execution = ExecutionMode.DIRECT
        runtime_type = RuntimeType.GENERIC
        runtime_id = "general_chat"
    elif route_mode == "clarify":
        interaction = InteractionMode.CLARIFY
        runtime_type = RuntimeType.GENERIC
        runtime_id = "clarify"
    elif route_mode == "handoff":
        interaction = InteractionMode.HANDOFF
        runtime_type = RuntimeType.GENERIC
        runtime_id = "handoff"
    else:
        canonical_route_mode = domain_graph_registry.resolve_alias(route_mode)
        graph = (
            domain_graph_registry.get(canonical_route_mode)
            if canonical_route_mode
            else None
        )
        if graph is not None:
            domain_name = graph.domain or graph.name
            runtime_type = graph.runtime_type
            execution = ExecutionMode.WORKFLOW
            runtime_id = graph.runtime_id or graph.name
            subflow = subflow or graph.decision_subflow or graph.subflow

    return RouteDecisionV2(
        domain=DomainDecisionV2(
            name=domain_name,
            subflow=subflow,
            confidence=confidence,
            source="compatibility",
            reasoning=reason,
        ),
        intent=IntentDecisionV2(
            name=route_mode,
            confidence=confidence,
            source="compatibility",
            reasoning=reason,
        ),
        execution=ExecutionDecision(mode=execution),
        interaction=InteractionDecision(mode=interaction),
        runtime=RuntimeTarget(
            type=runtime_type,
            id=runtime_id,
            subflow=subflow,
        ),
        capability=CapabilityDecisionV2(
            name=None,
            candidates=[],
            confidence=0.0,
            source="compatibility",
            reasoning=reason,
        ),
        workflow_name=workflow_name,
        confidence=confidence,
        reason=reason,
    )


def project_route_decision_to_legacy_state(
    decision: RouteDecisionV2,
    *,
    legacy_route_mode: str | None = None,
    legacy_route_decision: Mapping[str, Any] | None = None,
    legacy_execution_decision: Mapping[str, Any] | None = None,
    existing_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成旧 State 字段；生产代码只能通过此函数写路由兼容字段。"""

    route_mode = legacy_route_mode or decision.runtime.id
    candidates = [item.model_dump(mode="json") for item in (decision.capability.candidates if decision.capability else [])]
    capability_name = decision.capability.name if decision.capability else None
    meta = {
        "domain": decision.domain.name,
        "subflow": decision.domain.subflow,
        "domain_confidence": decision.domain.confidence,
        "domain_source": decision.domain.source,
        "intent": decision.intent.name,
        "intent_confidence": decision.intent.confidence,
        "intent_source": decision.intent.source,
        "runtime_type": decision.runtime.type.value,
        "runtime_id": decision.runtime.id,
        "interaction_mode": decision.interaction.mode.value,
        "execution_mode": decision.execution.mode.value,
        "candidate_tools": [item["name"] for item in candidates],
        "selected_tool": capability_name or "",
        "need_clarification": decision.interaction.mode is InteractionMode.CLARIFY,
        "clarification_reason": "interaction_clarify" if decision.interaction.mode is InteractionMode.CLARIFY else "",
        "route_mode": route_mode,
    }
    legacy_decision = legacy_route_decision or RouteDecision(
        execution_mode=decision.execution.mode,
        route_mode=route_mode,
        candidates=candidates,
        confidence=decision.confidence,
        reason=decision.reason,
        workflow_name=decision.workflow_name,
        routing_meta=meta,
    ).model_dump(mode="json")
    domain_decision = {
        "domain": decision.domain.name,
        "subflow": decision.domain.subflow,
        "confidence": decision.domain.confidence,
        "source": decision.domain.source,
        "reasoning": decision.domain.reasoning,
    }
    intent_decision = {
        "intent": decision.intent.name,
        "kind": (
            "domain_graph"
            if domain_graph_registry.resolve_alias(route_mode) is not None
            else route_mode
        ),
        "confidence": decision.intent.confidence,
        "source": decision.intent.source,
        "reasoning": decision.intent.reasoning,
        "execution_hint": decision.execution.mode.value,
        "candidate_names": [item["name"] for item in candidates],
    }
    capability_decision = {
        "domain": decision.domain.name,
        "capability": capability_name,
        "candidates": candidates,
        "confidence": decision.capability.confidence if decision.capability else 0.0,
        "source": decision.capability.source if decision.capability else "compatibility",
        "reasoning": decision.capability.reasoning if decision.capability else "",
    }
    execution_decision = dict(legacy_execution_decision or {
        "mode": decision.execution.mode.value,
        "target": decision.runtime.id,
        "confidence": decision.confidence,
        "reasoning": decision.reason or "",
    })
    result: dict[str, Any] = {
        "route_decision_v2": decision.model_dump(mode="json"),
        "route_decision": legacy_decision,
        "route_mode": route_mode,
        "domain": decision.domain.name,
        "domain_confidence": decision.domain.confidence,
        "domain_margin": 0.0,
        "domain_source": decision.domain.source,
        "candidate_tools": [item["name"] for item in candidates],
        "selected_tool": capability_name or "",
        "tool_arguments": None,
        "tool_confidence": decision.capability.confidence if decision.capability else 0.0,
        "tool_route_mode": "",
        "need_clarification": decision.interaction.mode is InteractionMode.CLARIFY,
        "clarification_reason": "interaction_clarify" if decision.interaction.mode is InteractionMode.CLARIFY else "",
        "domain_decision": domain_decision,
        "intent_decision": intent_decision,
        "capability_decision": capability_decision,
        "execution_decision": execution_decision,
        "router_fallback_reason": "",
        "legacy_used": False,
    }
    if existing_metadata:
        result["metadata"] = dict(existing_metadata)
    return result


def route_update_for_mode(
    route_mode: str,
    *,
    extra: Mapping[str, Any] | None = None,
    include_legacy_flat_fields: bool = True,
) -> dict[str, Any]:
    """为 prefilter/短路出口构造完整兼容更新。"""

    result = project_route_decision_to_legacy_state(
        build_route_decision_v2_from_route(route_mode),
        legacy_route_mode=route_mode,
    )
    if not include_legacy_flat_fields:
        for field in (
            "domain",
            "domain_confidence",
            "domain_margin",
            "domain_source",
            "candidate_tools",
            "selected_tool",
            "tool_arguments",
            "tool_confidence",
            "tool_route_mode",
            "need_clarification",
            "clarification_reason",
            "domain_decision",
            "intent_decision",
            "capability_decision",
            "execution_decision",
            "router_fallback_reason",
        ):
            result.pop(field, None)
    if extra:
        result.update(extra)
    return result


def build_route_decision_v2_from_engine(
    decision: RouteDecision,
    *,
    route_mode: str | None = None,
    domain_meta: Mapping[str, Any] | None = None,
) -> RouteDecisionV2:
    """将现有 RoutingEngine 决策转换为 V2，保留其真实证据。"""

    mode = route_mode or decision.route_mode or decision.execution_mode.value
    meta = dict(domain_meta or decision.routing_meta or {})
    base = build_route_decision_v2_from_route(
        mode,
        domain=meta.get("domain"),
        subflow=meta.get("subflow"),
        workflow_name=decision.workflow_name,
        confidence=decision.confidence,
        reason=decision.reason or "RoutingEngine 决策",
    )
    capability_name = meta.get("selected_tool")
    candidates = list(decision.candidates)
    capability = CapabilityDecisionV2(
        name=capability_name,
        candidates=candidates,
        confidence=float(meta.get("fine_top1_score") or decision.confidence or 0.0),
        source=str(meta.get("domain_source") or "route_engine"),
        reasoning=str(meta.get("intent_reasoning") or decision.reason or ""),
    )
    return base.model_copy(
        update={
            "capability": capability,
            "domain": base.domain.model_copy(update={
                "confidence": float(meta.get("domain_confidence") or base.domain.confidence),
                "source": str(meta.get("domain_source") or base.domain.source),
                "reasoning": str(meta.get("domain_reasoning") or base.domain.reasoning),
            }),
            "intent": base.intent.model_copy(update={
                "name": str(meta.get("intent") or base.intent.name),
                "confidence": float(meta.get("intent_confidence") or base.intent.confidence),
                "source": str(meta.get("intent_source") or base.intent.source),
                "reasoning": str(meta.get("intent_reasoning") or base.intent.reasoning),
            }),
        }
    )
