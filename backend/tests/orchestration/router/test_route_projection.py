"""RouteDecisionV2 → legacy state 投影测试。"""

import pytest

from backend.orchestration.router.projection import (
    build_route_decision_v2_from_route,
    project_route_decision_to_legacy_state,
)
from backend.orchestration.router.types import (
    CapabilityDecisionV2,
    DomainDecisionV2,
    ExecutionDecision,
    ExecutionMode,
    InteractionDecision,
    InteractionMode,
    IntentDecisionV2,
    RouteDecisionV2,
    RuntimeTarget,
    RuntimeType,
)


def _make_decision(
    *,
    execution: ExecutionMode,
    runtime_type: RuntimeType,
    runtime_id: str,
    interaction: InteractionMode = InteractionMode.EXECUTE,
    domain: str = "general",
) -> RouteDecisionV2:
    return RouteDecisionV2(
        domain=DomainDecisionV2(
            name=domain,
            confidence=0.91,
            source="rule",
            reasoning="测试",
        ),
        intent=IntentDecisionV2(
            name="test_intent",
            confidence=0.91,
            source="rule",
            reasoning="测试",
        ),
        execution=ExecutionDecision(mode=execution),
        interaction=InteractionDecision(mode=interaction),
        runtime=RuntimeTarget(type=runtime_type, id=runtime_id),
        capability=CapabilityDecisionV2(
            name="sql.query" if execution is ExecutionMode.DIRECT else None,
            candidates=[],
            confidence=0.8,
            source="rule",
            reasoning="测试",
        ),
        confidence=0.91,
        reason="测试",
    )


@pytest.mark.parametrize(
    ("route_mode", "decision", "expected_interaction", "expected_runtime"),
    [
        (
            "direct",
            _make_decision(
                execution=ExecutionMode.DIRECT,
                runtime_type=RuntimeType.DIRECT,
                runtime_id="direct",
            ),
            "execute",
            "direct_runtime",
        ),
        (
            "plan",
            _make_decision(
                execution=ExecutionMode.PLAN,
                runtime_type=RuntimeType.PLAN,
                runtime_id="capability_plan",
            ),
            "execute",
            "plan_runtime",
        ),
        (
            "workflow",
            _make_decision(
                execution=ExecutionMode.WORKFLOW,
                runtime_type=RuntimeType.WORKFLOW,
                runtime_id="daily_report",
            ),
            "execute",
            "workflow_runtime",
        ),
        (
            "customer_service",
            _make_decision(
                execution=ExecutionMode.WORKFLOW,
                runtime_type=RuntimeType.AGENT,
                runtime_id="customer_service",
                domain="customer_service",
            ),
            "execute",
            "agent_runtime",
        ),
        (
            "travel",
            _make_decision(
                execution=ExecutionMode.WORKFLOW,
                runtime_type=RuntimeType.WORKFLOW,
                runtime_id="travel",
                domain="travel",
            ),
            "execute",
            "workflow_runtime",
        ),
        (
            "selection_funnel",
            _make_decision(
                execution=ExecutionMode.WORKFLOW,
                runtime_type=RuntimeType.WORKFLOW,
                runtime_id="selection_funnel",
                domain="selection",
            ),
            "execute",
            "workflow_runtime",
        ),
        (
            "clarify",
            _make_decision(
                execution=ExecutionMode.PLAN,
                runtime_type=RuntimeType.GENERIC,
                runtime_id="clarify",
                interaction=InteractionMode.CLARIFY,
            ),
            "clarify",
            "generic_runtime",
        ),
        (
            "handoff",
            _make_decision(
                execution=ExecutionMode.PLAN,
                runtime_type=RuntimeType.GENERIC,
                runtime_id="handoff",
                interaction=InteractionMode.HANDOFF,
            ),
            "handoff",
            "generic_runtime",
        ),
        (
            "general_chat",
            _make_decision(
                execution=ExecutionMode.DIRECT,
                runtime_type=RuntimeType.GENERIC,
                runtime_id="general_chat",
            ),
            "execute",
            "generic_runtime",
        ),
    ],
)
def test_projection_preserves_legacy_route_mode_and_exposes_v2(
    route_mode, decision, expected_interaction, expected_runtime,
):
    result = project_route_decision_to_legacy_state(
        decision,
        legacy_route_mode=route_mode,
    )

    assert result["route_mode"] == route_mode
    assert result["route_decision_v2"]["interaction"]["mode"] == expected_interaction
    assert result["route_decision_v2"]["runtime"]["type"] == expected_runtime
    assert result["route_decision"]["route_mode"] == route_mode


def test_projection_emits_legacy_domain_and_execution_snapshots():
    decision = _make_decision(
        execution=ExecutionMode.DIRECT,
        runtime_type=RuntimeType.DIRECT,
        runtime_id="direct",
        domain="business",
    )

    result = project_route_decision_to_legacy_state(
        decision,
        legacy_route_mode="direct",
    )

    assert result["domain"] == "business"
    assert result["domain_confidence"] == 0.91
    assert result["domain_source"] == "rule"
    assert result["selected_tool"] == "sql.query"
    assert result["candidate_tools"] == []
    assert result["execution_decision"]["mode"] == "direct"
    assert result["legacy_used"] is False


@pytest.mark.parametrize(
    ("route_mode", "expected_execution", "expected_runtime", "expected_interaction"),
    [
        ("direct", "direct", RuntimeType.DIRECT, InteractionMode.EXECUTE),
        ("plan", "plan", RuntimeType.PLAN, InteractionMode.EXECUTE),
        ("workflow", "workflow", RuntimeType.WORKFLOW, InteractionMode.EXECUTE),
        ("customer_service", "workflow", RuntimeType.AGENT, InteractionMode.EXECUTE),
        ("travel", "workflow", RuntimeType.WORKFLOW, InteractionMode.EXECUTE),
        ("selection_funnel", "workflow", RuntimeType.WORKFLOW, InteractionMode.EXECUTE),
        ("clarify", "plan", RuntimeType.GENERIC, InteractionMode.CLARIFY),
        ("handoff", "plan", RuntimeType.GENERIC, InteractionMode.HANDOFF),
        ("general_chat", "direct", RuntimeType.GENERIC, InteractionMode.EXECUTE),
    ],
)
def test_build_route_decision_v2_from_legacy_route(
    route_mode, expected_execution, expected_runtime, expected_interaction,
):
    decision = build_route_decision_v2_from_route(route_mode)

    assert decision.execution.mode.value == expected_execution
    assert decision.runtime.type is expected_runtime
    assert decision.interaction.mode is expected_interaction


def test_projection_accepts_existing_legacy_metadata_without_mutating_it():
    decision = build_route_decision_v2_from_route("travel")
    existing = {"request_id": "r1", "route_mode": "old"}

    result = project_route_decision_to_legacy_state(
        decision,
        legacy_route_mode="travel",
        existing_metadata=existing,
    )

    assert existing == {"request_id": "r1", "route_mode": "old"}
    assert result["metadata"]["request_id"] == "r1"
