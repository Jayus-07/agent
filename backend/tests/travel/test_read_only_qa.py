"""草案期间只读问答：不得改写计划状态或触碰持久 checkpoint。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _itinerary() -> dict:
    return {
        "plan_version": 4,
        "status": "ready",
        "brief": {
            "destination": "厦门",
            "days": 2,
            "pace": "relaxed",
            "budget_cny": 1500,
            "preferences": ["海边"],
        },
        "days": [
            {
                "day_index": 1,
                "items": [
                    {"title": "环岛路", "start": "09:00", "end": "11:00", "minutes": 120,
                     "note": "上午安排海边步行"},
                ],
                "cost_cny": 80,
            },
            {
                "day_index": 2,
                "items": [
                    {"title": "鼓浪屿", "start": "10:00", "end": "13:00", "minutes": 180,
                     "note": "预留轮渡时间"},
                ],
                "cost_cny": 120,
            },
        ],
        "cost": {"tickets": 200, "meals": 300, "lodging": 600,
                 "transit": 100, "total": 1200},
    }


def test_read_only_change_request_is_rejected_without_a_change_or_llm_call(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.travel.slot_filler import slot_filler_node

    def unexpected(*_args, **_kwargs):
        raise AssertionError("规则已识别为改单时，不应启动规划理解链")

    monkeypatch.setattr(
        "backend.travel.services.turn_decision_service.interpret_turn_with_llm",
        unexpected,
    )
    result = slot_filler_node({
        "request_mode": "read_only",
        "user_message": "把第二天换成室内景点",
        "brief": _itinerary()["brief"],
        "itinerary": _itinerary(),
    })

    assert result["intent"] == "read_only_blocked"
    assert result["turn_decision"]["primary_action"] == "answer"
    assert result["turn_decision"]["changes"] == []
    assert result["turn_decision"]["additional_tasks"] == []
    assert "itinerary" not in result
    assert "brief" not in result


def test_read_only_context_question_accepts_only_an_answer_decision(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.travel.core.intent import TravelTurnDecision
    from backend.travel.services import turn_decision_service
    from backend.travel.slot_filler import slot_filler_node

    monkeypatch.setattr(
        turn_decision_service,
        "interpret_turn_with_llm",
        lambda *_args, **_kwargs: SimpleNamespace(
            decision=TravelTurnDecision(
                primary_action="answer", confidence=0.95, parse_source="llm"),
            meta=lambda: {"status": "ok", "model": "test"},
        ),
    )
    result = slot_filler_node({
        "request_mode": "read_only",
        "user_message": "这趟为什么这样安排？",
        "brief": _itinerary()["brief"],
        "itinerary": _itinerary(),
    })

    assert result["intent"] == "read_only_qa"
    assert result["turn_decision"]["primary_action"] == "answer"
    assert result["turn_decision"]["changes"] == []
    assert result["partial_replan"] == {}


def test_supervisor_read_only_gate_cannot_dispatch_partial_replan() -> None:
    from backend.travel.supervisor import TravelStage, decide

    decision = decide({
        "request_mode": "read_only",
        "intent": "read_only_blocked",
        "partial_replan": {"operation": "replace"},
        "turn_decision": {"primary_action": "modify_plan"},
    })

    assert decision.stage is TravelStage.REPORT


def test_supervisor_only_dispatches_declared_read_only_query_tools() -> None:
    from backend.travel.supervisor import TravelStage, decide

    decision = decide({
        "request_mode": "read_only",
        "intent": "read_only_qa",
        "turn_decision": {
            "additional_tasks": [{"type": "query_weather", "task_id": "weather-1"}],
        },
    })
    assert decision.stage is TravelStage.AUXILIARY_TASKS

    unsafe = decide({
        "request_mode": "read_only",
        "intent": "read_only_qa",
        "turn_decision": {
            "additional_tasks": [{"type": "create_plan", "task_id": "plan-1"}],
        },
    })
    assert unsafe.stage is TravelStage.REPORT


def test_read_only_answer_uses_versioned_plan_cost_as_estimate() -> None:
    from backend.travel.reporter import _assemble

    answer = _assemble({
        "intent": "read_only_qa",
        "user_message": "这趟预算怎么算的？",
        "request_mode": "read_only",
        "itinerary": _itinerary(),
    })

    assert "草案 v4" in answer
    assert "¥1200" in answer
    assert "估算" in answer
    assert "¥1500" in answer


def test_read_only_question_result_never_returns_or_republishes_itinerary() -> None:
    from backend.travel.models.graph_result import build_travel_graph_result

    result = build_travel_graph_result({
        "intent": "read_only_qa",
        "itinerary": _itinerary(),
        "final_answer": "草案预算估算为 ¥1200。",
        "brief_missing": [],
    })

    assert result["status"] == "answered"
    assert result["itinerary"] is None


def test_read_only_graph_is_compiled_without_a_checkpointer(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.travel import graph_builder

    calls = []
    monkeypatch.setattr(
        graph_builder, "build_travel_graph",
        lambda *, checkpointer=None: calls.append(checkpointer) or object(),
    )
    monkeypatch.setattr(graph_builder, "_travel_read_only_graph", None)

    graph_builder.get_travel_read_only_graph()
    graph_builder.get_travel_read_only_graph()

    assert calls == [None]


def test_read_only_route_context_uses_scoped_draft_over_active(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.travel.core.plan_service as service_module
    from backend.app.api.routes import travel as travel_route

    calls = []

    class _Versions:
        def latest_version(self, conversation_id, user_id, *, tenant_id, strict=False):
            calls.append(("latest", conversation_id, user_id, tenant_id))
            assert strict is True
            return {
                "plan_version": 4,
                "plan_status": "waiting_confirmation",
                "itinerary": _itinerary(),
            }

        def active_version(self, conversation_id, user_id, *, tenant_id, strict=False):
            calls.append(("active", conversation_id, user_id, tenant_id))
            assert strict is True
            return {
                "plan_version": 3,
                "plan_status": "confirmed",
                "itinerary": {**_itinerary(), "plan_version": 3},
            }

    monkeypatch.setattr(service_module, "plan_version_service", _Versions())
    context = travel_route._load_read_only_plan_context(
        "trip-1", "user-15", "tenant-a")

    assert context["active_plan_version"] == 3
    assert context["draft_plan_version"] == 4
    assert context["reference_itinerary"]["plan_version"] == 4
    assert calls == [
        ("latest", "trip-1", "user-15", "tenant-a"),
        ("active", "trip-1", "user-15", "tenant-a"),
    ]


def test_read_only_request_mode_is_part_of_api_contract() -> None:
    from backend.app.api.routes.travel import TravelPlanRequest

    request = TravelPlanRequest(message="这趟预算怎么算？", mode="read_only")
    assert request.mode == "read_only"
