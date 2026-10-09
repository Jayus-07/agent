"""TravelTurnDecision 对外可观察的契约与调度行为。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.travel.core.intent import TravelTurnDecision
from backend.travel.models.brief import TravelBrief


def test_turn_decision_rejects_unapproved_actions_and_task_tools():
    """模型不能绕过 capability 白名单指定任意工具或执行动作。"""
    with pytest.raises(ValidationError):
        TravelTurnDecision(primary_action="execute_tool")

    with pytest.raises(ValidationError):
        TravelTurnDecision(
            primary_action="answer",
            additional_tasks=[{
                "task_id": "task-1",
                "type": "query_train",
                "params": {"origin": "福州", "destination": "厦门",
                           "tool": "arbitrary.delete"},
            }],
        )


def test_turn_decision_carries_a_plan_and_an_independent_train_task():
    decision = TravelTurnDecision(
        primary_action="create_plan",
        additional_tasks=[{
            "task_id": "train-1",
            "type": "query_train",
            "params": {"origin": "福州", "destination": "厦门"},
        }],
        confidence=0.97,
        parse_source="rule",
    )

    assert decision.primary_action == "create_plan"
    assert decision.additional_tasks[0].type == "query_train"
    params = decision.additional_tasks[0].params
    assert (params.origin, params.destination) == ("福州", "厦门")


def test_turn_decision_rejects_duplicate_task_ids():
    with pytest.raises(ValidationError):
        TravelTurnDecision(
            primary_action="answer",
            additional_tasks=[
                {"task_id": "task-1", "type": "query_weather"},
                {"task_id": "task-1", "type": "query_train"},
            ],
        )


def test_turn_decision_limits_auxiliary_task_count_and_duplicate_kinds():
    task = {"type": "query_weather", "params": {"city": "厦门"}}
    with pytest.raises(ValidationError):
        TravelTurnDecision(
            primary_action="answer",
            additional_tasks=[
                {**task, "task_id": f"task-{index}"}
                for index in range(5)
            ],
        )
    with pytest.raises(ValidationError):
        TravelTurnDecision(
            primary_action="answer",
            additional_tasks=[
                {**task, "task_id": "weather-1"},
                {**task, "task_id": "weather-2"},
            ],
        )


def _quiet_slot_filler(monkeypatch):
    from backend.config import travel as travel_config
    from backend.travel import graph_builder
    from backend.travel.services import city_guide_service
    from backend.travel.services import inspiration_service, live_search_service

    monkeypatch.setattr(travel_config, "TRAVEL_LLM_INTENT_ENABLED", False)
    monkeypatch.setattr(travel_config, "TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED", False)
    monkeypatch.setattr(travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", False)
    monkeypatch.setattr(graph_builder, "get_persistence_status", lambda: "ready")
    monkeypatch.setattr(city_guide_service, "prime_city_guide", lambda _city: None)
    monkeypatch.setattr(
        inspiration_service, "fetch_destination_inspiration",
        lambda _city: {"status": "ok", "items": []},
    )
    monkeypatch.setattr(
        live_search_service, "search_trains",
        lambda **_kwargs: {"status": "ok", "trains": []},
    )


def test_form_plan_keeps_plan_primary_and_train_as_additional_task(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    update = slot_filler_node({
        "user_message": "查高铁",
        "request_mode": "plan",
        "brief_input": {"destination": "厦门", "origin": "福州", "days": 2},
    })

    assert update["turn_decision"]["primary_action"] == "create_plan"
    assert update["brief"]["destination"] == "厦门"
    assert update["brief"]["days"] == 2
    assert update["turn_decision"]["additional_tasks"][0]["type"] == "query_train"
    assert update["turn_decision"]["additional_tasks"][0]["params"]["origin"] == "福州"


def test_free_text_city_days_and_train_does_not_collapse_to_transit_only(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    update = slot_filler_node({"user_message": "厦门2天，查高铁"})

    assert update["turn_decision"]["primary_action"] == "create_plan"
    assert update["brief"]["destination"] == "厦门"
    assert update["brief"]["days"] == 2
    assert [task["type"] for task in update["turn_decision"]["additional_tasks"]] == [
        "query_train",
    ]


def test_transit_only_question_does_not_write_a_trip_brief(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    update = slot_filler_node({"user_message": "明天福州到厦门最快的高铁"})

    assert update["turn_decision"]["primary_action"] == "answer"
    assert update["turn_decision"]["additional_tasks"][0]["type"] == "query_train"
    params = update["turn_decision"]["additional_tasks"][0]["params"]
    assert params["origin"] == "福州"
    assert params["destination"] == "厦门"
    assert params["travel_date"]
    assert "brief" not in update


def test_static_question_does_not_increment_existing_brief_version(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    previous = TravelBrief(destination="厦门", days=2, version=3).model_dump()
    update = slot_filler_node({
        "user_message": "厦门有什么好吃的",
        "brief": previous,
        "itinerary": {"plan_version": 3},
    })

    assert update["turn_decision"]["primary_action"] == "answer"
    assert update["brief"]["version"] == 3
    assert update.get("brief_changed_fields", []) == []


def test_explicit_replan_keeps_weather_as_an_independent_task(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    previous = TravelBrief(
        destination="厦门", days=2, budget_cny=5000, version=3,
    ).model_dump()
    update = slot_filler_node({
        "user_message": "改为4天，预算2000元，顺便查天气",
        "brief": previous,
        "itinerary": {"plan_version": 3},
    })

    assert update["turn_decision"]["primary_action"] == "replan_plan"
    assert update["brief"]["days"] == 4
    assert update["brief"]["budget_cny"] == 2000
    assert [task["type"] for task in update["turn_decision"]["additional_tasks"]] == [
        "query_weather",
    ]


def test_unverified_selected_poi_reference_asks_which_attraction(monkeypatch):
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    previous = TravelBrief(destination="厦门", days=2, version=3).model_dump()
    update = slot_filler_node({
        "user_message": "这个景点换掉",
        "brief": previous,
        "itinerary": {"plan_version": 3, "days": [
            {"day_index": 1, "items": []},
            {"day_index": 2, "items": []},
        ]},
        "ui_context": {"selected_day": 2, "selected_poi_id": "not-in-trip"},
    })

    assert update["turn_decision"]["needs_clarification"] is True
    assert "selected_poi_id" in update["turn_decision"]["missing_fields"]
    assert "哪个景点" in update["clarifications"][0]


def test_ui_context_only_exposes_pois_belonging_to_the_selected_day():
    from backend.travel.slot_filler import _verified_ui_context

    itinerary = {"days": [
        {"day_index": 1, "items": []},
        {"day_index": 2, "items": [{
            "poi": {"poi_id": "poi-xm-1", "name": "鼓浪屿"},
        }]},
    ]}

    valid = _verified_ui_context({
        "itinerary": itinerary,
        "ui_context": {"selected_day": 2, "selected_poi_id": "poi-xm-1"},
    })
    invalid = _verified_ui_context({
        "itinerary": itinerary,
        "ui_context": {"selected_day": 1, "selected_poi_id": "poi-xm-1"},
    })

    assert valid["selected_poi_id"] == "poi-xm-1"
    assert valid["selected_poi_name"] == "鼓浪屿"
    assert "selected_poi_id" not in invalid


def test_ui_context_rejects_poi_when_selected_day_is_invalid():
    from backend.travel.slot_filler import _verified_ui_context

    context = _verified_ui_context({
        "itinerary": {"days": [{"day_index": 1, "items": [{
            "title": "鼓浪屿", "poi": {"poi_id": "poi-xm-1", "name": "鼓浪屿"},
        }]}]},
        "ui_context": {"selected_day": 99, "selected_poi_id": "poi-xm-1"},
    })
    assert context == {}


def test_llm_change_cannot_invent_selected_poi_or_replacement():
    from backend.travel.core.intent import TravelChange
    from backend.travel.slot_filler import (
        _sanitize_llm_changes,
        _verified_ui_context,
    )

    state = {"itinerary": {"days": [{"day_index": 1, "items": [{
        "title": "鼓浪屿", "poi": {"poi_id": "poi-xm-1", "name": "鼓浪屿"},
    }]}]}}
    ui_context = _verified_ui_context({
        **state,
        "ui_context": {"selected_day": 1, "selected_poi_id": "poi-xm-1"},
    })
    proposed = TravelChange.model_validate({
        "op": "replace_poi",
        "scope": {"day_index": 99, "poi_id": "invented-poi"},
        "target_name": "虚构旧景点",
        "replacement_name": "月亮湾",
        "evidence_text": "这个景点换掉",
    })

    safe, missing = _sanitize_llm_changes(
        [proposed], state, "这个景点换掉", ui_context)

    assert safe[0]["scope"] == {"day_index": 1, "poi_id": "poi-xm-1", "time_slot": None}
    assert safe[0]["target_name"] == "鼓浪屿"
    assert safe[0]["replacement_name"] is None
    assert missing == ["replacement_poi"]


def test_invalid_explicit_day_does_not_fall_back_to_ui_selection():
    from backend.travel.core.intent import TravelChange
    from backend.travel.slot_filler import _sanitize_llm_changes

    state = {"itinerary": {"days": [{
        "day_index": 1, "items": [{
            "title": "鼓浪屿", "poi": {"poi_id": "poi-xm-1", "name": "鼓浪屿"},
        }],
    }]}}
    change = TravelChange.model_validate({
        "op": "remove_poi",
        "scope": {"day_index": 1, "poi_id": "poi-xm-1"},
        "target_name": "鼓浪屿",
        "evidence_text": "第99天这个景点删掉",
    })

    safe, missing = _sanitize_llm_changes(
        [change], state, "第99天这个景点删掉",
        {"selected_day": 1, "selected_poi_id": "poi-xm-1"},
    )

    assert safe[0]["scope"]["day_index"] is None
    assert safe[0]["scope"]["poi_id"] is None
    assert "target_day" in missing


def test_slot_filler_turns_unverified_llm_change_into_clarification(monkeypatch):
    from backend.config import travel as travel_config
    from backend.travel.services import turn_decision_service
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    monkeypatch.setattr(
        travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", True)
    decision = TravelTurnDecision(
        primary_action="modify_plan",
        changes=[{
            "op": "replace_poi",
            "scope": {"day_index": 99, "poi_id": "invented-poi"},
            "target_name": "虚构旧景点",
            "replacement_name": "月亮湾",
            "evidence_text": "这个景点换掉",
        }],
        parse_source="llm",
    )
    monkeypatch.setattr(
        turn_decision_service, "interpret_turn_with_llm",
        lambda *_args, **_kwargs: turn_decision_service.TurnDecisionOutcome(
            decision=decision, status="ok", model="test-model",
        ),
    )

    update = slot_filler_node({
        "user_message": "这个景点换掉",
        "brief": TravelBrief(destination="厦门", days=1).model_dump(),
        "itinerary": {"plan_version": 1, "days": [{
            "day_index": 1, "items": [{
                "title": "鼓浪屿",
                "poi": {"poi_id": "poi-xm-1", "name": "鼓浪屿"},
            }],
        }]},
        "ui_context": {"selected_day": 1, "selected_poi_id": "poi-xm-1"},
    })

    actual = update["turn_decision"]
    assert actual["changes"][0]["scope"]["poi_id"] == "poi-xm-1"
    assert actual["changes"][0]["target_name"] == "鼓浪屿"
    assert actual["changes"][0]["replacement_name"] is None
    assert actual["needs_clarification"] is True
    assert "replacement_poi" in actual["missing_fields"]
    assert update["clarifications"]


def test_trace_projects_decision_tasks_changes_and_llm_attribution():
    from backend.travel.trace_semantics import build_trace_semantics

    trace = build_trace_semantics({
        "intent": "modify",
        "turn_decision": {
            "primary_action": "modify_plan",
            "changes": [{"op": "set_pace", "scope": {"day_index": 2}}],
            "additional_tasks": [{"type": "query_weather"}],
            "confidence": 0.9,
            "parse_source": "llm",
            "needs_clarification": False,
        },
        "turn_decision_meta": {
            "status": "ok", "model": "test-model",
            "prompt_version": "travel.turn_decision@v2",
            "latency_ms": 120, "input_tokens": 11, "output_tokens": 7,
        },
    }, {})

    assert trace["primary_action"] == "modify_plan"
    assert trace["additional_task_types"] == ["query_weather"]
    assert trace["modification_operations"] == ["set_pace"]
    assert trace["turn_decision_model"] == "test-model"
    assert trace["turn_decision_prompt_version"] == "travel.turn_decision@v2"
    assert trace["turn_decision_input_tokens"] == 11
    assert trace["turn_decision_output_tokens"] == 7


def test_complex_turn_uses_only_the_unified_requirement_call(monkeypatch):
    from backend.config import travel as travel_config
    from backend.travel.services import turn_decision_service
    from backend.travel.slot_filler import slot_filler_node

    _quiet_slot_filler(monkeypatch)
    monkeypatch.setattr(
        travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", True)
    calls = []

    def interpret(message, *, context, timeout_ms=None):
        calls.append((message, context))
        return turn_decision_service.TurnDecisionOutcome(
            decision=TravelTurnDecision(
                primary_action="answer", confidence=0.88, parse_source="llm",
            ),
            status="ok", fallback_reason="", model="test-model",
            prompt_version="travel.turn_decision@v-test", latency_ms=10,
        )

    monkeypatch.setattr(
        turn_decision_service, "interpret_turn_with_llm", interpret)
    monkeypatch.setattr(
        "backend.travel.services.llm_intent_service.classify_intent_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("旧意图服务不得再调用")),
    )
    monkeypatch.setattr(
        "backend.travel.services.llm_slot_enrichment_service.enrich_slots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("旧槽位服务不得再串调")),
    )
    from backend.travel.services import inspiration_service
    monkeypatch.setattr(
        inspiration_service, "fetch_destination_inspiration",
        lambda _city: {"status": "ok", "items": []},
    )

    update = slot_filler_node({"user_message": "厦门推荐一个亲子友好景点"})

    assert len(calls) == 1
    assert update["turn_decision"]["primary_action"] == "answer"
    assert update.get("brief") is None
