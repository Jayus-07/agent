"""旅游 API 异步响应与实际工作寿命分离的回归测试。"""
import asyncio
import importlib
import json
import threading
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

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


def _decision_payload():
    return {
        "items": [{
            "code": "TIME_CLOSED_WEEKDAY",
            "message": "福建博物院当天闭馆，是否仍要保留？",
            "poi_name": "福建博物院",
        }],
        "options": {"keep": "保留", "drop": "移除"},
    }


def _decision_snapshot(payload=None, checkpoint_id="checkpoint-1"):
    interrupts = (SimpleNamespace(value=payload),) if payload else ()
    return SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=interrupts)] if payload else [],
        values={},
        config={"configurable": {"checkpoint_id": checkpoint_id}},
    )


def _decision_token(payload, *, thread_id="travel:t:u:travel-v2:execution",
                    checkpoint_id="checkpoint-1"):
    return travel._bind_travel_decision_token(
        payload, _decision_snapshot(payload, checkpoint_id), thread_id,
    )["decision_token"]


def _frame_payload(frames, event_name):
    matching = [item for item in frames if item.startswith(f"event: {event_name}")]
    assert matching, f"missing {event_name!r} frame: {frames!r}"
    frame = matching[0]
    return json.loads(frame.split("data: ", 1)[1])


@pytest.mark.asyncio
async def test_v2_interrupt_is_clarification_and_does_not_write_trip(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 5.0
    payload = _decision_payload()
    state = {"pending": False}

    class Graph:
        def get_state(self, _config):
            return (_decision_snapshot(payload) if state["pending"]
                    else _decision_snapshot())

        def invoke(self, _graph_input, config):
            assert config["configurable"]["thread_id"] == (
                "travel:t:u:travel-v2:execution"
            )
            state["pending"] = True
            return {"__interrupt__": [SimpleNamespace(value=payload)]}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: pytest.fail("中断结果不能被当成成功行程"),
    )
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)
    monkeypatch.setattr(
        travel, "_record_plan_version",
        lambda *_args, **_kwargs: pytest.fail("中断不能写入正式行程版本"),
    )

    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]

    assert result["status"] == "needs_clarification"
    assert result["result_kind"] == "clarification"
    assert result["itinerary"] is None
    pending = result["travel_context"]["pending_decision"]
    assert {key: value for key, value in pending.items()
            if key != "decision_token"} == payload
    assert pending["decision_token"] == _decision_token(payload)
    assert "移除：地点名" in result["final_answer"]


@pytest.mark.asyncio
async def test_v2_explicit_decision_resumes_same_scoped_checkpoint(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    payload = _decision_payload()
    captured = {}
    token = _decision_token(payload)

    class Graph:
        def get_state(self, config):
            captured["thread_id"] = config["configurable"]["thread_id"]
            return _decision_snapshot(payload)

        def invoke(self, command, config):
            captured["command"] = command
            captured["invoke_thread_id"] = config["configurable"]["thread_id"]
            return {"itinerary": {"plan_version": 4}}

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_travel_resume_lock", lambda *_: nullcontext())
    monkeypatch.setattr(
        "backend.travel.models.graph_result.build_travel_graph_result",
        lambda _state: {
            "status": "success",
            "result_kind": "plan",
            "final_answer": "行程已生成",
            "itinerary": {"plan_version": 4},
        },
    )
    trace_modes = []
    monkeypatch.setattr(
        travel, "_finish_travel_trace", lambda *args: trace_modes.append(args[-1]),
    )
    async def load(_request):
        return travel.TravelPlanRequest(
            message="移除：福建博物院",
            conversation_id="execution",
            action_payload={"decision_token": token},
        )

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]

    assert captured["thread_id"] == "travel:t:u:travel-v2:execution"
    assert captured["invoke_thread_id"] == captured["thread_id"]
    assert captured["command"].resume == {"drop": ["福建博物院"]}
    assert result["status"] == "success"
    assert result["itinerary"]["plan_version"] == 4
    assert trace_modes == ["interrupt_resume"]


@pytest.mark.asyncio
async def test_pending_interrupt_does_not_consume_ordinary_question(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    payload = _decision_payload()

    class Graph:
        def get_state(self, _config):
            return _decision_snapshot(payload)

        def invoke(self, *_args, **_kwargs):
            pytest.fail("普通问答不得恢复中断或修改行程")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)

    async def load(_request):
        return travel.TravelPlanRequest(
            message="附近有什么咖啡店？", conversation_id="execution",
        )

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]

    assert result["status"] == "needs_clarification"
    assert result["itinerary"] is None
    assert result["travel_context"]["pending_decision"]["decision_token"] == (
        _decision_token(payload)
    )
    assert "没有被当作确认" in result["final_answer"]


@pytest.mark.asyncio
async def test_repeated_decision_without_pending_checkpoint_is_not_replayed(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    captured = []

    class Graph:
        def get_state(self, config):
            captured.append(config["configurable"]["thread_id"])
            return SimpleNamespace(tasks=[], values={})

        def invoke(self, *_args, **_kwargs):
            pytest.fail("已完成的确认不能作为新规划请求重放")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)
    async def load(_request):
        return travel.TravelPlanRequest(message="保留", conversation_id="execution")

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]

    assert result["status"] == "answered"
    assert result["itinerary"] is None
    assert "没有待处理" in result["final_answer"]
    assert captured == ["travel:t:u:travel-v2:execution"]


@pytest.mark.asyncio
async def test_decision_from_other_tenant_cannot_resume_checkpoint(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    captured = {}

    class Graph:
        def get_state(self, config):
            captured["thread_id"] = config["configurable"]["thread_id"]
            return SimpleNamespace(tasks=[], values={})

        def invoke(self, *_args, **_kwargs):
            pytest.fail("另一租户的确认不能进入图")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)
    monkeypatch.setattr(
        travel, "require_identity",
        lambda _request: SimpleNamespace(user_id="bob", tenant_id="other"),
    )
    async def load(_request):
        return travel.TravelPlanRequest(message="保留", conversation_id="execution")

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]

    assert captured["thread_id"] == "travel:other:bob:travel-v2:execution"
    assert result["status"] == "answered"
    assert "pending_decision" not in result["travel_context"]


@pytest.mark.asyncio
async def test_old_decision_token_cannot_apply_to_new_pending_interrupt(
    setup_runtime, monkeypatch,
):
    setup_runtime.timeout_s = 1.0
    old_payload = _decision_payload()
    current_payload = _decision_payload()
    current_payload["items"][0]["poi_name"] = "鼓岭"
    old_token = _decision_token(old_payload, checkpoint_id="old-checkpoint")

    class Graph:
        def get_state(self, _config):
            return _decision_snapshot(current_payload, "new-checkpoint")

        def invoke(self, *_args, **_kwargs):
            pytest.fail("旧决策令牌不得恢复新的待决冲突")

    monkeypatch.setattr("backend.travel.graph_builder.get_travel_graph", lambda: Graph())
    monkeypatch.setattr(travel, "_finish_travel_trace", lambda *_: None)

    async def load(_request):
        return travel.TravelPlanRequest(
            message="保留",
            conversation_id="execution",
            action_payload={"decision_token": old_token},
        )

    monkeypatch.setattr(travel, "_load_travel_plan_request", load)
    response = await travel.travel_v2_plan_stream(request())
    result = _frame_payload(await _frames(response), "done")["result"]
    pending = result["travel_context"]["pending_decision"]

    assert result["status"] == "needs_clarification"
    assert pending["decision_token"] == _decision_token(
        current_payload, checkpoint_id="new-checkpoint",
    )
    assert "事项已更新" in result["final_answer"]
    assert "没有修改正式行程" in result["final_answer"]


def test_postgres_resume_lock_rejects_a_concurrent_resume(monkeypatch):
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            statements.append((statement, params))

        def fetchone(self):
            return (False,)

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    connection = Connection()
    psycopg = ModuleType("psycopg")
    psycopg.connect = lambda **_kwargs: connection
    monkeypatch.setitem(__import__("sys").modules, "psycopg", psycopg)
    monkeypatch.setattr(
        "backend.travel.graph_builder.get_persistence_status", lambda: "healthy",
    )

    with pytest.raises(travel._TravelResumeInProgressError):
        with travel._travel_resume_lock("thread-1"):
            pytest.fail("未获取共享恢复锁时不能执行 Command(resume)")

    assert "pg_try_advisory_lock" in statements[0][0]
    assert connection.closed


def test_resume_trace_records_state_version_and_mode(monkeypatch):
    from backend.observability import tracer

    trace = SimpleNamespace(
        tags={}, metadata={}, session_id="travel-v2:execution",
    )
    monkeypatch.setattr(
        "backend.travel.trace_semantics.build_trace_semantics",
        lambda *_args, **_kwargs: {"conversation_id": "travel-v2:execution"},
    )
    monkeypatch.setattr(tracer.trace_collector, "finish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.observability.question_ledger.record_question_from_trace",
        lambda *_args, **_kwargs: None,
    )

    travel._finish_travel_trace(
        trace,
        0.0,
        {
            "status": "success",
            "result_kind": "draft",
            "active_plan_version": 2,
            "draft_plan_version": 3,
            "itinerary": {"plan_version": 3},
        },
        {"conversation_id": "travel-v2:execution"},
        "run-3",
        [],
        "interrupt_resume",
    )

    assert trace.tags["travel_resume_mode"] == "interrupt_resume"
    assert trace.tags["travel_status"] == "success"
    assert trace.tags["travel_result_kind"] == "draft"
    assert trace.tags["travel_active_plan_version"] == "2"
    assert trace.tags["travel_draft_plan_version"] == "3"
    assert trace.tags["travel_graph_plan_version"] == "3"


async def _frames(response):
    return [frame async for frame in response.body_iterator]
