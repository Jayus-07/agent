# -*- coding: utf-8 -*-
"""旅游 REST/SSE 请求 Trace 测试。"""

from types import SimpleNamespace

import pytest

from backend.app.api.routes import travel
from backend.observability.tracer import trace_collector


@pytest.fixture(autouse=True)
def _trace_isolation():
    trace_collector.clear_for_test()
    yield
    trace_collector.clear_for_test()


def _request():
    return SimpleNamespace()


@pytest.mark.asyncio
async def test_travel_plan_rest_creates_and_finishes_trace(monkeypatch):
    captured = []

    class FakeGraph:
        def invoke(self, *_args, **_kwargs):
            return {"brief": {"destination": "杭州"}}

    monkeypatch.setattr(travel, "require_identity", lambda _request: SimpleNamespace(user_id="u-1"))
    monkeypatch.setattr(
        travel, "_load_travel_plan_request",
        lambda _request: _resolved(travel.TravelPlanRequest(
            message="杭州两日游", session_id="s-1", conversation_id="c-1",
        )),
    )
    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: FakeGraph())
    monkeypatch.setattr("backend.orchestration.graph.travel_graph_node._build_invoke_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {"status": "success", "final_answer": "ok", "itinerary": None},
    )
    original_finish = trace_collector.finish

    def capture_finish(trace, *args, **kwargs):
        captured.append(trace)
        return original_finish(trace, *args, **kwargs)

    monkeypatch.setattr(trace_collector, "finish", capture_finish)

    result = await travel.travel_plan(_request())

    assert result["status"] == "success"
    assert len(captured) == 1
    assert captured[0].workflow_name == "agent"
    assert captured[0].tags["travel_status"] == "success"
    assert captured[0].tags["travel_run_id"].startswith("travel-")
    assert captured[0].tags["travel_destination"] == "杭州"


@pytest.mark.asyncio
async def test_travel_plan_stream_binds_trace_inside_worker(monkeypatch):
    captured = []

    class FakeGraph:
        def invoke(self, *_args, **_kwargs):
            current = trace_collector.current()
            captured.append(current)
            return {"brief": {"destination": "上海"}}

    monkeypatch.setattr(travel, "require_identity", lambda _request: SimpleNamespace(user_id="u-2"))
    monkeypatch.setattr(
        travel, "_load_travel_plan_request",
        lambda _request: _resolved(travel.TravelPlanRequest(
            message="上海两日游", session_id="s-2", conversation_id="c-2",
        )),
    )
    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: FakeGraph())
    monkeypatch.setattr("backend.orchestration.graph.travel_graph_node._build_invoke_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {"status": "success", "final_answer": "ok", "itinerary": None},
    )

    response = await travel.travel_plan_stream(_request())
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert chunks
    assert captured and captured[0] is not None
    assert captured[0].workflow_name == "agent"
    assert captured[0].tags["travel_destination"] == "上海"


async def _resolved(value):
    return value
