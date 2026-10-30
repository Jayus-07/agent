"""独立旅游入口使用可信身份隔离 checkpoint。

V1 REST 规划端点已退役；V2 规划流（唯一现行入口）用
``travel-v2:{conversation_id}`` 独立会话命名空间执行，身份隔离语义由
``_build_invoke_config`` 的 ``travel:{tenant}:{user}:{conv}`` 命名空间
保证——两个用户同 conversation 不共享 thread。
"""
from types import SimpleNamespace

import pytest

from backend.app.api.routes import travel


@pytest.mark.asyncio
async def test_two_users_same_conversation_get_distinct_threads(monkeypatch):
    configs = []

    class Graph:
        def invoke(self, state, config):
            configs.append(config["configurable"]["thread_id"])
            return {}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda state: {"status": "answered", "final_answer": "ok", "itinerary": None},
    )

    async def load(request):
        return travel.TravelPlanRequest(message="泉州3天", conversation_id="same")

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    monkeypatch.setattr(travel, "require_identity", lambda request: request.identity)
    for uid in ("alice", "bob"):
        request = SimpleNamespace(identity=SimpleNamespace(user_id=uid, tenant_id="tenant"))
        response = await travel.travel_v2_plan_stream(request)
        frames = [chunk async for chunk in response.body_iterator]
        assert any('event: done' in chunk for chunk in frames)
    assert configs == [
        "travel:tenant:alice:travel-v2:same",
        "travel:tenant:bob:travel-v2:same",
    ]


def test_user_identity_still_scopes_thread_without_tenant():
    from backend.orchestration.graph.travel_graph_node import _build_invoke_config

    a = _build_invoke_config("same", user_id="alice")
    b = _build_invoke_config("same", user_id="bob")
    assert a["configurable"]["thread_id"] != b["configurable"]["thread_id"]
