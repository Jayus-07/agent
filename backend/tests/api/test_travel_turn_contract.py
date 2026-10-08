"""旅游双端轮次请求与响应契约的跨语言回归测试。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.api.routes.travel import (
    TravelPlanRequest,
    TravelResponseMetadata,
    TravelUiContext,
    _build_travel_graph_input,
    _attach_travel_response_metadata,
)
from backend.travel.models.brief import TravelBrief


_REPO_ROOT = Path(__file__).resolve().parents[3]
_TRAVEL_TS = _REPO_ROOT / "frontend/src/api/travel.ts"


def _typescript_fields(interface_name: str) -> set[str]:
    source = _TRAVEL_TS.read_text(encoding="utf-8")
    match = re.search(
        rf"export interface {interface_name} \{{([^{{}}]*)\}}", source, re.S)
    assert match, f"TypeScript interface {interface_name} 不存在"
    return {
        field
        for line in match.group(1).splitlines()
        if (field_match := re.match(r"\s*([a-z_]+)\??\s*:", line))
        for field in [field_match.group(1)]
    }


def test_legacy_request_defaults_and_new_structured_fields() -> None:
    legacy = TravelPlanRequest(message="厦门两天")
    assert legacy.mode == "plan"
    assert legacy.brief_input is None
    assert legacy.base_plan_version is None
    assert legacy.ui_context == TravelUiContext()
    assert legacy.action_payload == {}

    request = TravelPlanRequest(
        message="厦门两天，顺便查高铁",
        mode="plan",
        brief_input={"destination": "厦门", "days": 2, "party_size": 2},
        base_plan_version=3,
        ui_context={"selected_day": 2, "selected_poi_id": "poi-1"},
        action_payload={"operation": "replace_poi"},
    )
    assert isinstance(request.brief_input, TravelBrief)
    assert request.brief_input.destination == "厦门"
    assert request.brief_input.days == 2
    assert request.ui_context.selected_day == 2
    assert request.base_plan_version == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "厦门", "mode": "unknown"},
        {"message": "厦门", "base_plan_version": 0},
        {"message": "厦门", "ui_context": {"selected_day": 0}},
    ],
)
def test_invalid_turn_contract_is_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TravelPlanRequest.model_validate(payload)


def test_response_metadata_distinguishes_draft_and_preserves_version_refs() -> None:
    result = _attach_travel_response_metadata(
        {
            "status": "success",
            "final_answer": "已生成草案",
            "plan_status": "waiting_confirmation",
            "itinerary": {"plan_version": 4},
        },
        conversation_id="conv-1",
        turn_id="turn-1",
        base_plan_version=3,
    )

    metadata = TravelResponseMetadata.model_validate(result)
    assert metadata.result_kind == "draft"
    assert metadata.turn_id == "turn-1"
    assert metadata.conversation_id == "conv-1"
    assert metadata.active_plan_version is None
    assert metadata.draft_plan_version == 4
    assert metadata.base_plan_version == 3
    assert metadata.task_results == []


def test_structured_request_is_passed_into_graph_state() -> None:
    request = TravelPlanRequest(
        message="厦门两天，顺便查高铁",
        mode="plan",
        brief_input={"destination": "厦门", "days": 2},
        base_plan_version=3,
        ui_context={"selected_day": 2},
        action_payload={"operation": "replace_poi"},
    )
    identity = type("Identity", (), {"user_id": "u-1"})()

    state = _build_travel_graph_input(
        request, identity, "conv-1", "turn-1")

    assert state["request_mode"] == "plan"
    assert state["brief_input"] == {"destination": "厦门", "days": 2}
    assert state["base_plan_version"] == 3
    assert state["ui_context"] == {"selected_day": 2}
    assert state["action_payload"] == {"operation": "replace_poi"}
    assert state["turn_id"] == "turn-1"


def test_python_and_typescript_turn_contract_fields_match() -> None:
    assert _typescript_fields("TravelPlanRequestPayload") == set(
        TravelPlanRequest.model_fields)
    assert _typescript_fields("TravelUiContext") == set(
        TravelUiContext.model_fields)
    assert _typescript_fields("TravelResponseMetadata") == set(
        TravelResponseMetadata.model_fields)
    assert _typescript_fields("TravelBriefInput") <= set(
        TravelBrief.model_fields)
