"""附加旅游任务调度与独立失败语义。"""
from __future__ import annotations

from backend.travel.services.live_search_service import LiveSearchError


def _task(task_id: str, task_type: str, **params) -> dict:
    return {
        "task_id": task_id,
        "type": task_type,
        "params": params,
    }


def test_auxiliary_train_query_returns_real_result_and_empty_status(monkeypatch):
    from backend.travel.auxiliary_tasks import auxiliary_tasks_node
    from backend.travel.services import live_search_service

    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        return {"trains": [], "source": "12306"}

    monkeypatch.setattr(live_search_service, "search_trains", fake_search)
    update = auxiliary_tasks_node({
        "user_message": "明天福州到厦门最快的高铁",
        "turn_decision": {"additional_tasks": [
            _task("train-1", "query_train", origin="福州",
                  destination="厦门", travel_date="2026-10-09"),
        ]},
        "task_results": [],
        "notes": [],
    })

    assert calls == [{
        "from_station": "福州", "to_station": "厦门",
        "travel_date": "2026-10-09", "limit": 6,
    }]
    assert update["transit_query"]["status"] == "ok"
    assert update["task_results"][0]["status"] == "success"
    assert update["task_results"][0]["data_status"] == "empty"
    assert update["task_results"][0]["result_count"] == 0


def test_optional_train_failure_is_recorded_without_blocking(monkeypatch):
    from backend.travel.auxiliary_tasks import auxiliary_tasks_node
    from backend.travel.services import live_search_service

    def fail(**_kwargs):
        raise LiveSearchError("12306 unavailable")

    monkeypatch.setattr(live_search_service, "search_trains", fail)
    update = auxiliary_tasks_node({
        "user_message": "明天福州到厦门最快的高铁",
        "turn_decision": {"additional_tasks": [
            _task("train-1", "query_train", origin="福州",
                  destination="厦门", travel_date="2026-10-09"),
        ]},
        "task_results": [],
        "notes": [],
    })

    assert update["transit_query"]["status"] == "failed"
    assert update["task_results"][0]["status"] == "degraded"
    assert update["blocked_tools"] == []
    assert update["degraded_tools"][0]["provider"] == "12306"


def test_multiple_optional_failures_are_accumulated(monkeypatch):
    from backend.travel.auxiliary_tasks import auxiliary_tasks_node
    from backend.travel.services import live_search_service, weather_service

    def fail_train(**_kwargs):
        raise LiveSearchError("train provider unavailable")

    def fail_weather(_city):
        return None, "weather provider unavailable"

    monkeypatch.setattr(live_search_service, "search_trains", fail_train)
    monkeypatch.setattr(weather_service, "fetch_forecast", fail_weather)
    update = auxiliary_tasks_node({
        "user_message": "明天福州到厦门高铁，厦门天气",
        "turn_decision": {"additional_tasks": [
            _task("train-1", "query_train", origin="福州",
                  destination="厦门", travel_date="2026-10-09"),
            _task("weather-1", "query_weather", city="厦门"),
        ]},
        "task_results": [],
        "notes": [],
    })

    assert [result["status"] for result in update["task_results"]] == [
        "degraded", "degraded",
    ]
    assert len(update["degraded_tools"]) == 2
    assert len(update["tool_failures"]) == 2
    assert update["blocked_tools"] == []


def test_auxiliary_weather_result_is_rendered_without_generic_no_source_claim(
    monkeypatch,
):
    from backend.travel.auxiliary_tasks import auxiliary_tasks_node
    from backend.travel.reporter import _assemble
    from backend.travel.services import weather_service

    monkeypatch.setattr(weather_service, "fetch_forecast", lambda _city: ({
        "days": [{"date": "2026-10-09", "day": {"weather": "小雨"}}],
    }, ""))
    task = _task("weather-1", "query_weather", city="厦门")
    task_state = {
        "intent": "query_dynamic",
        "query_destination": "厦门",
        "turn_decision": {"additional_tasks": [task]},
        "task_results": [],
        "notes": [],
    }
    task_state.update(auxiliary_tasks_node(task_state))

    answer = _assemble(task_state)
    assert "2026-10-09" in answer
    assert "小雨" in answer
    assert "还没有可核验的实时来源" not in answer


def test_real_graph_runs_auxiliary_task_then_reports_without_planning_experts(
    monkeypatch,
):
    from backend.travel import graph_builder
    from backend.travel.services import live_search_service

    calls = []
    monkeypatch.setattr(
        live_search_service, "search_trains",
        lambda **kwargs: calls.append(kwargs) or {
            "trains": [], "source": "12306",
        },
    )

    def slot_result(_state):
        return {
            "turn_decision": {"additional_tasks": [
                _task("train-1", "query_train", origin="福州",
                      destination="厦门", travel_date="2026-10-09"),
            ]},
            "intent": "query_transit",
            "brief": {},
            "brief_missing": ["days"],
            "clarifications": [],
            "query_destination": "厦门",
            "task_results": [],
            "transit_query": {},
            "notes": [],
            "finished": False,
        }

    monkeypatch.setattr(graph_builder, "slot_filler_node", slot_result)
    graph = graph_builder.build_travel_graph()
    result = graph.invoke({"user_message": "明天福州到厦门高铁"})

    assert calls == [{
        "from_station": "福州", "to_station": "厦门",
        "travel_date": "2026-10-09", "limit": 6,
    }]
    assert result["task_results"][0]["status"] == "success"
    assert result.get("expert_history", []) == []
    assert "暂未查到直达车次" in result["final_answer"]


def test_slot_filler_only_builds_transit_task_and_performs_no_io(monkeypatch):
    from backend.config import travel as travel_config
    from backend.travel.services import live_search_service
    from backend.travel.slot_filler import slot_filler_node

    monkeypatch.setattr(
        travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", False)
    monkeypatch.setattr(
        live_search_service, "search_trains",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("slot_filler 不得执行外部 Tool")),
    )
    update = slot_filler_node({
        "user_message": "明天从福州到厦门最快的高铁",
    })

    assert update["turn_decision"]["primary_action"] == "answer"
    assert update["turn_decision"]["additional_tasks"][0]["type"] == "query_train"
    assert update["transit_query"] == {}


def test_graph_result_includes_independent_task_results():
    from backend.travel.models.graph_result import build_travel_graph_result

    results = [{"task_id": "train-1", "type": "query_train",
                "status": "success", "data_status": "available"}]
    result = build_travel_graph_result({
        "intent": "query_transit",
        "brief_missing": ["days"],
        "task_results": results,
        "transit_query": {"status": "ok", "trains": []},
    })
    assert result["task_results"] == results
