"""旅游 API 异步响应与实际工作寿命分离的回归测试。"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend.app.api.routes import travel
from backend.travel.request_runtime import RequestExecutor


@pytest.fixture
def setup_runtime(monkeypatch):
    executor = RequestExecutor(workers=2, timeout_s=0.06)
    monkeypatch.setattr("backend.travel.request_runtime.get_request_executor", lambda: executor)
    monkeypatch.setattr(travel, "require_identity", lambda r: r.identity)
    async def load(r):
        return travel.TravelPlanRequest(message="泉州3天", conversation_id="execution")
    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    monkeypatch.setattr("backend.travel.models.graph_result.build_travel_graph_result", lambda s: {
        "status": "answered", "final_answer": "ok", "itinerary": None,
    })
    yield executor
    executor.shutdown()


def request():
    return SimpleNamespace(identity=SimpleNamespace(user_id="u", tenant_id="t"))


@pytest.mark.asyncio
async def test_rest_does_not_block_event_loop(setup_runtime, monkeypatch):
    started, release = threading.Event(), threading.Event()
    class Graph:
        def invoke(self, *args, **kwargs):
            started.set()
            release.wait(0.3)
            return {}
    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    task = asyncio.create_task(travel.travel_plan(request()))
    try:
        await asyncio.sleep(0.01)
        assert started.is_set()
        assert not task.done(), "同步 invoke 不得阻塞事件循环直到工作完成"
    finally:
        release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_timeout_does_not_record_late_result(setup_runtime, monkeypatch, stream):
    release = threading.Event()
    records = []
    class Graph:
        def invoke(self, *args, **kwargs):
            release.wait(1)
            return {}
    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_record_plan_version", lambda *args: records.append(args))
    try:
        if stream:
            response = await travel.travel_plan_stream(request())
            frames = await asyncio.wait_for(_frames(response), 0.8)
            assert '"error_type": "timeout"' in ''.join(frames)
            assert 'event: done' not in ''.join(frames)
        else:
            result = await asyncio.wait_for(travel.travel_plan(request()), 0.8)
            assert result["status"] == "failed"
            assert result["error_type"] == "timeout"
    finally:
        release.set()
    await asyncio.sleep(0.05)
    assert records == []


async def _frames(response):
    return [frame async for frame in response.body_iterator]
