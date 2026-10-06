"""Phase 0：旅游交互门与派生指纹回归。"""
from __future__ import annotations

import pytest

from backend.travel.core.intent import TravelIntent, classify_intent
from backend.travel.graph_builder import build_travel_graph
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.models.graph_result import build_travel_graph_result
from backend.travel.models.itinerary import Itinerary
from backend.travel.reporter import _assemble
from backend.travel.services.requirement_service import RequirementService
from backend.travel.slot_filler import slot_filler_node
from backend.travel.supervisor import decide


def _state(previous: TravelBrief, message: str, *, stale: bool = False) -> dict:
    return {
        "user_message": message,
        "brief": previous.model_dump(),
        "brief_fingerprint": "stale-phase0-fp" if stale
        else brief_fingerprint(previous),
        "itinerary": {"plan_version": 3},
        "user_id": "phase0-test-user",
    }


@pytest.fixture
def deterministic_slot_filler(monkeypatch):
    """Phase 0 单测只验证控制流，不接 LLM、偏好库或异步城市预热。"""
    from backend.config import travel as travel_config

    monkeypatch.setattr(travel_config, "TRAVEL_LLM_INTENT_ENABLED", False)
    monkeypatch.setattr(travel_config, "TRAVEL_PREFS_ENABLED", False)
    monkeypatch.setattr(
        "backend.travel.services.city_guide_service.prime_city_guide",
        lambda _destination: None,
    )


@pytest.mark.parametrize(
    "message,expected",
    [
        ("谢谢", TravelIntent.SOCIAL),
        ("好的", TravelIntent.SOCIAL),
        ("辛苦了", TravelIntent.SOCIAL),
        ("你是模型吗", TravelIntent.META),
        ("你能做什么", TravelIntent.META),
    ],
)
def test_phase0_classifies_social_and_meta_before_trip_mutation(
    message: str, expected: TravelIntent,
):
    assert classify_intent(message, has_itinerary=True) is expected


def test_modify_gate_catches_day_pace_request():
    assert classify_intent("第一天别太赶", has_itinerary=True) is TravelIntent.MODIFY


@pytest.mark.parametrize("intent", [
    "out_of_scope", "social", "meta", "query_static", "query_dynamic",
])
def test_non_planning_intent_exits_at_supervisor(intent: str):
    decision = decide({"intent": intent, "brief_missing": []})
    assert decision.action == "finish_report"


def test_query_city_is_subject_not_trip_destination(deterministic_slot_filler):
    previous = TravelBrief(destination="上海", days=3)
    update = slot_filler_node(
        _state(previous, "杭州有什么好吃的？", stale=True),
    )

    assert update["intent"] == TravelIntent.QUERY_STATIC.value
    assert update["brief"]["destination"] == "上海"
    assert update["query_destination"] == "杭州"
    assert update["brief_fingerprint"] == brief_fingerprint(previous)
    assert "brief_change_reason" not in update
    assert "需求已变化" not in update["notes"]
    assert "itinerary" not in update


def test_first_lightweight_message_does_not_seed_empty_trip_state(
    deterministic_slot_filler,
):
    update = slot_filler_node({"user_message": "谢谢"})

    assert update["intent"] == TravelIntent.SOCIAL.value
    assert "brief" not in update
    assert "brief_fingerprint" not in update


def test_non_planning_message_never_resets_on_degraded_persistence(
    deterministic_slot_filler, monkeypatch,
):
    from backend.config import travel as travel_config

    monkeypatch.setattr(
        "backend.travel.graph_builder.get_persistence_status",
        lambda: "degraded",
    )
    monkeypatch.setattr(travel_config, "TRAVEL_REQUIRE_PERSISTENCE", True)

    previous = TravelBrief(destination="上海", days=3)
    update = slot_filler_node(_state(previous, "谢谢", stale=True))

    assert update["intent"] == TravelIntent.SOCIAL.value
    assert "planning_reset" not in update
    assert update["brief"] == previous.model_dump()
    assert update["brief_fingerprint"] == brief_fingerprint(previous)


@pytest.mark.parametrize("message,expected_intent", [
    ("谢谢", TravelIntent.SOCIAL.value),
    ("好的", TravelIntent.SOCIAL.value),
    ("你是模型吗", TravelIntent.META.value),
])
def test_noop_message_does_not_reset_stale_state(
    deterministic_slot_filler, message: str, expected_intent: str,
):
    previous = TravelBrief(destination="上海", days=3)
    update = slot_filler_node(_state(previous, message, stale=True))

    assert update["intent"] == expected_intent
    assert update["brief"] == previous.model_dump()
    assert update["brief_fingerprint"] == brief_fingerprint(previous)
    assert "brief_change_reason" not in update
    assert "需求已变化" not in update["notes"]
    assert "itinerary" not in update


def test_fingerprint_change_is_derived_from_authoritative_brief():
    previous = TravelBrief(destination="上海", days=3)
    service = RequirementService()

    detection = service.detect_brief_change(
        previous, previous.model_copy(), "stale-phase0-fp",
    )

    assert detection["changed"] is False
    assert detection["fingerprint"] == brief_fingerprint(previous)


@pytest.mark.parametrize("message,expected_intent", [
    ("谢谢", TravelIntent.SOCIAL.value),
    ("好的", TravelIntent.SOCIAL.value),
    ("辛苦了", TravelIntent.SOCIAL.value),
    ("你是模型吗", TravelIntent.META.value),
    ("杭州有什么好吃的？", TravelIntent.QUERY_STATIC.value),
    ("西湖值得去吗？", TravelIntent.QUERY_STATIC.value),
])
def test_graph_early_exit_never_enters_expert_chain(
    deterministic_slot_filler, message: str, expected_intent: str,
):
    previous = TravelBrief(destination="上海", days=3)
    graph = build_travel_graph()
    itinerary = Itinerary(brief=previous, days=[], plan_version=3)

    state = _state(previous, message, stale=True)
    state["itinerary"] = itinerary.model_dump()
    final = graph.invoke(state)

    assert final["intent"] == expected_intent
    assert final["itinerary"]["plan_version"] == 3
    assert final["brief"]["destination"] == "上海"
    assert final["brief_fingerprint"] == brief_fingerprint(previous)
    assert final.get("expert_history", []) == []
    assert final["final_answer"]


@pytest.mark.parametrize("intent", ["social", "meta", "out_of_scope"])
def test_answered_result_does_not_republish_old_plan(intent: str):
    result = build_travel_graph_result({
        "intent": intent,
        "brief_missing": [],
        "itinerary": {"plan_version": 3},
        "final_answer": "已回答",
    })

    assert result["status"] == "answered"
    assert result["itinerary"] is None


@pytest.mark.parametrize(
    "intent,expected",
    [("social", "不用客气"), ("meta", "旅游规划")],
)
def test_social_and_meta_have_lightweight_answers(intent: str, expected: str):
    assert expected in _assemble({"intent": intent})
