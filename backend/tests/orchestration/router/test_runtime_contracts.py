"""Runtime 语义 V2 契约测试。"""

import pytest
from pydantic import ValidationError

from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.router.types import (
    CapabilityDecisionV2,
    DomainDecisionV2,
    ExecutionDecision,
    ExecutionMode,
    InteractionDecision,
    InteractionMode,
    IntentDecisionV2,
    RouteDecisionV2,
    RuntimeContext,
    RuntimeResult,
    RuntimeTarget,
    RuntimeType,
    normalize_runtime_result,
)


def _decision(
    *,
    runtime_type: RuntimeType,
    runtime_id: str,
    interaction: InteractionMode,
    execution: ExecutionMode = ExecutionMode.WORKFLOW,
):
    return RouteDecisionV2(
        domain=DomainDecisionV2(
            name="travel",
            subflow=None,
            confidence=0.94,
            source="vector",
            reasoning="测试",
        ),
        intent=IntentDecisionV2(
            name="trip_plan",
            confidence=0.94,
            source="rule",
            reasoning="测试",
        ),
        execution=ExecutionDecision(mode=execution),
        interaction=InteractionDecision(mode=interaction),
        runtime=RuntimeTarget(
            type=runtime_type,
            id=runtime_id,
            subflow=None,
        ),
        capability=CapabilityDecisionV2(
            name=None,
            candidates=[],
            confidence=0.0,
            source="rule",
            reasoning="测试",
        ),
        confidence=0.94,
        reason="测试",
    )


def test_route_decision_v2_contains_four_orthogonal_dimensions():
    decision = _decision(
        runtime_type=RuntimeType.WORKFLOW,
        runtime_id="travel",
        interaction=InteractionMode.EXECUTE,
    )

    assert decision.domain.name == "travel"
    assert decision.execution.mode is ExecutionMode.WORKFLOW
    assert decision.interaction.mode is InteractionMode.EXECUTE
    assert decision.runtime.type is RuntimeType.WORKFLOW
    assert decision.runtime.id == "travel"


def test_runtime_type_enum_contains_all_five_runtime_families():
    assert {item.value for item in RuntimeType} == {
        "agent_runtime",
        "workflow_runtime",
        "plan_runtime",
        "direct_runtime",
        "generic_runtime",
    }


def test_invalid_runtime_and_interaction_combination_is_rejected():
    with pytest.raises(ValidationError):
        _decision(
            runtime_type="not_a_runtime",
            runtime_id="travel",
            interaction=InteractionMode.EXECUTE,
        )

    with pytest.raises(ValidationError):
        _decision(
            runtime_type=RuntimeType.WORKFLOW,
            runtime_id="travel",
            interaction="unknown",
        )

    with pytest.raises(ValidationError):
        _decision(
            runtime_type=RuntimeType.PLAN,
            runtime_id="capability_plan",
            interaction=InteractionMode.EXECUTE,
            execution=ExecutionMode.WORKFLOW,
        )


def test_runtime_result_normalizes_cs_travel_and_selection_outputs():
    cs = normalize_runtime_result("客服回答", runtime_id="customer_service")
    travel = normalize_runtime_result(
        {"itinerary": [{"day": 1}], "sources": ["poi-1"]},
        runtime_id="travel",
    )
    selection = normalize_runtime_result(
        {
            "answer": "选品报告",
            "answer_type": "markdown",
            "ui_payload": {"funnel": "done"},
        },
        runtime_id="selection_funnel",
    )

    assert cs.answer == "客服回答"
    assert cs.metadata["runtime_id"] == "customer_service"
    assert travel.ui_payload["itinerary"] == [{"day": 1}]
    assert travel.sources == ["poi-1"]
    assert selection.answer == "选品报告"
    assert selection.answer_type == "markdown"
    assert selection.ui_payload == {"funnel": "done"}


def test_runtime_result_preserves_clarification_handoff_tool_calls_and_metadata():
    result = RuntimeResult(
        status="success",
        answer="已完成",
        clarification={"question": "需要城市"},
        handoff={"target_domain": "travel"},
        tool_calls=[{"tool_id": "travel.poi.search"}],
        metadata={"confidence": 0.9},
    )

    serialized = result.model_dump(mode="json")

    assert serialized["clarification"] == {"question": "需要城市"}
    assert serialized["handoff"] == {"target_domain": "travel"}
    assert serialized["tool_calls"] == [{"tool_id": "travel.poi.search"}]
    assert serialized["metadata"] == {"confidence": 0.9}


def test_runtime_context_is_checkpoint_safe():
    context = RuntimeContext(
        session_id="session-1",
        tenant_id="default",
        user_id="user-1",
        question="规划福州三日游",
        route_decision=_decision(
            runtime_type=RuntimeType.WORKFLOW,
            runtime_id="travel",
            interaction=InteractionMode.EXECUTE,
        ),
        state={"source": "test"},
    )

    assert context.model_dump(mode="json")["route_decision"]["runtime"]["id"] == "travel"


def test_domain_graph_runtime_descriptor_defaults_are_backward_compatible():
    graph = DomainGraph(
        name="test_domain",
        node_name="test_domain_node",
        label="测试域",
        adapter=lambda state: state,
    )

    assert graph.runtime_id == "test_domain"
    assert graph.runtime_type is RuntimeType.WORKFLOW
    assert graph.supports_checkpoint is False
    assert graph.supports_interrupt is False
