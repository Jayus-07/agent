"""旅游域真实事件出口契约。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.travel.core.events import (
    run_travel_tool,
    travel_event_scope,
)


def test_tool_events_are_delivered_to_the_active_stream_sink() -> None:
    events: list[dict] = []

    with travel_event_scope(events.append):
        result = run_travel_tool(
            "travel.search_poi",
            "research",
            lambda: [{"poi_id": "p1"}],
            result_summary=lambda value: {"result_count": len(value)},
        )

    assert result == [{"poi_id": "p1"}]
    assert [event["event"] for event in events] == [
        "tool.started",
        "tool.result",
    ]
    assert events[0]["tool"] == "travel.search_poi"
    assert events[1]["status"] == "success"
    assert events[1]["result_count"] == 1


def test_tool_failure_is_emitted_and_propagated_without_a_fake_result() -> None:
    events: list[dict] = []

    def fail() -> None:
        raise RuntimeError("provider unavailable")

    with travel_event_scope(events.append):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            run_travel_tool("travel.weather.query", "research", fail)

    assert [event["event"] for event in events] == [
        "tool.started",
        "tool.result",
    ]
    assert events[-1]["status"] == "failed"
    assert events[-1]["error_type"] == "RuntimeError"


def test_plan_stream_exposes_real_tool_events_and_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.api.routes import travel as travel_route

    class FakeGraph:
        def invoke(self, _input: dict, config: dict | None = None) -> dict:
            candidates = run_travel_tool(
                "travel.search_poi",
                "research",
                lambda: [{"poi_id": "p1"}],
                result_summary=lambda value: {"result_count": len(value)},
            )
            return {
                "final_answer": "已生成行程",
                "brief": {"destination": "福州", "days": 1},
                "candidates": candidates,
                "itinerary": {"plan_version": 1, "days": []},
                "validation": None,
                "clarifications": [],
                "travel_context": {},
            }

    monkeypatch.setattr(
        "backend.travel.graph_builder.get_travel_graph", lambda: FakeGraph(),
    )
    monkeypatch.setattr(
        "backend.travel.core.plan_service.plan_version_service",
        type("PlanService", (), {
            "record_plan_result": staticmethod(lambda *_args: {}),
        })(),
    )

    app = FastAPI()
    app.include_router(travel_route.router)
    response = TestClient(app).post(
        "/travel/plan/stream",
        json={"message": "福州1天", "conversation_id": "conv-1"},
        headers={"X-User-Id": "15", "X-User-Name": "Mint", "X-Auth-Type": "jwt"},
    )

    assert response.status_code == 200
    frames = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    events = [__import__("json").loads(frame) for frame in frames]
    event_names = [event["event"] for event in events]
    assert "tool.started" in event_names
    assert "tool.result" in event_names
    assert events[-1]["event"] == "done"
    assert events[-1]["result"]["itinerary"]["plan_version"] == 1
    assert len({event["run_id"] for event in events}) == 1
    assert [event["seq"] for event in events] == list(
        range(1, len(events) + 1))
    assert sum(event["event"] in {"done", "error"} for event in events) == 1


def test_graph_node_wrapper_reports_the_actual_node_failure() -> None:
    from backend.travel.graph_builder import _evented_node

    events: list[dict] = []

    def fail(_state: dict) -> dict:
        raise ValueError("route data broken")

    with travel_event_scope(events.append):
        with pytest.raises(ValueError, match="route data broken"):
            _evented_node("travel_transit_expert", fail)({})

    assert [event["event"] for event in events] == [
        "stage.started",
        "stage.finished",
    ]
    assert events[-1]["status"] == "failed"
    assert events[-1]["stage"] == "travel_transit_expert"


def test_risk_tool_failure_marks_the_expert_failed_without_continuing_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    from backend.tests.travel.conftest import make_itinerary
    from backend.travel.experts import risk
    from backend.travel.models.brief import TravelBrief

    brief = TravelBrief(destination="福州", days=1, start_date=date(2026, 10, 2))
    itinerary = make_itinerary(brief=brief)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("knowledge provider unavailable")

    monkeypatch.setattr(risk, "retrieve_knowledge", fail)
    events: list[dict] = []
    state = {
        "brief": brief.model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [],
    }

    with travel_event_scope(events.append):
        update = risk.risk_expert_node(state)

    assert update["last_expert_result"]["status"] == "failed"
    assert update["last_expert_result"]["error"] == "knowledge provider unavailable"
    assert [event["event"] for event in events] == [
        "tool.started",
        "tool.result",
    ]
    assert events[-1]["status"] == "failed"


def test_graph_result_cannot_turn_a_failed_expert_into_success() -> None:
    from backend.travel.models.graph_result import build_travel_graph_result

    result = build_travel_graph_result({
        "final_answer": "行程草案",
        "brief": {"destination": "福州", "days": 1},
        "candidates": [{"poi_id": "p1"}],
        "itinerary": {"days": []},
        "expert_history": [{"expert": "risk", "status": "failed"}],
    })

    assert result["status"] == "failed"
