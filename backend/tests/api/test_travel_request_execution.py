"""旅游 API 异步响应与实际工作寿命分离的回归测试。"""
import asyncio
import importlib
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
    setup_runtime.timeout_s = 1.0
    # 预热 worker 内的首次导入，避免冷导入时间混入事件循环行为断言。
    for module_name in (
        "backend.observability.tracer",
        "backend.orchestration.graph.travel_graph_node",
        "backend.travel.graph_state",
    ):
        importlib.import_module(module_name)
    started, release = threading.Event(), threading.Event()

    class Graph:
        def invoke(self, *args, **kwargs):
            started.set()
            release.wait(0.3)
            return {}
    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    # 本测试只验证同步图调用被移出事件循环；输入构造、账本和 Trace
    # 分别由契约/集成测试覆盖，不能让冷启动与外部 I/O 混入本用例计时。
    monkeypatch.setattr(travel, "_build_travel_graph_input", lambda *_: {})
    monkeypatch.setattr(travel, "_seed_cross_turn_base", lambda *_: None)
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)
    task = asyncio.create_task(travel.travel_plan(request()))
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait, 0.5), 0.6)
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


@pytest.mark.asyncio
async def test_v2_stream_uses_isolated_session_without_v1_plan_ledger(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    captured = {}

    class Graph:
        def invoke(self, graph_input, config):
            captured["graph_input"] = graph_input
            captured["config"] = config
            return {"conversation_id": graph_input["conversation_id"]}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {
            "status": "success",
            "final_answer": "泉州行程已生成",
            "itinerary": {"brief": {"destination": "泉州"}, "days": []},
        },
    )
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)
    for name in (
        "_restore_from_chat_reference",
        "_latest_pending_draft",
        "_seed_cross_turn_base",
        "_record_plan_version",
        "_attach_trace_plan_projection",
    ):
        monkeypatch.setattr(
            travel, name,
            lambda *args, _name=name, **kwargs: pytest.fail(f"V2 不得调用 { _name }"),
        )

    submitted = {}
    submit = travel._submit_travel_request

    def capture_submit(identity, conversation_id, worker):
        submitted["conversation_id"] = conversation_id
        return submit(identity, conversation_id, worker)

    monkeypatch.setattr(travel, "_submit_travel_request", capture_submit)
    response = await travel.travel_v2_plan_stream(request())
    frames = await _frames(response)

    done_frames = [frame for frame in frames if frame.startswith("event: done")]
    assert len(done_frames) == 1, "\n".join(frames)
    done_frame = done_frames[0]
    payload = __import__("json").loads(done_frame.split("data: ", 1)[1])
    assert payload["result"]["itinerary"]["brief"]["destination"] == "泉州"
    assert captured["graph_input"]["conversation_id"] == "travel-v2:execution"
    assert captured["graph_input"]["session_id"] == "travel-v2:execution"
    assert "travel-v2:execution" in submitted["conversation_id"]


async def _frames(response):
    return [frame async for frame in response.body_iterator]
