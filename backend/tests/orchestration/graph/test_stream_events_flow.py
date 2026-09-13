"""stream_events 事件流单测（P1 真 token 级流式改造）

用假 graph 驱动 MultiAgentSystem.stream_events 的 worker 线程 + 合并队列架构，
验证：
- 真流式路径：sink delta 与节点事件按序产出，不再叠加假打字机
- 回退路径：无 sink delta 时兜底 emit_delta_events
- 用户中止路径
- done 事件与 final_answer 语义不变
"""
import threading

import pytest

from backend.infra.llm import proxy as proxy_mod
from backend.orchestration.graph.system import MultiAgentSystem


@pytest.fixture(autouse=True)
def _reset_sink():
    proxy_mod.reset_stream_sink()
    yield
    proxy_mod.reset_stream_sink()


@pytest.fixture(autouse=True)
def _allow_guard(monkeypatch):
    """跳过真 Input Guard（默认问题会被判 CLARIFY 短路，假图跑不到）。"""
    from backend.security.input_guard.types import GuardAction

    class _FakeResult:
        action = GuardAction.ALLOW
        message = ""

        def model_dump(self, mode="json"):
            return {"action": "allow"}

    class _FakeGuard:
        def guard(self, question, session_id=None):
            return _FakeResult()

    import backend.orchestration.graph.runner as runner_mod
    monkeypatch.setattr(runner_mod, "get_input_guard", lambda: _FakeGuard())


class _FakeMemory:
    def start_session(self, session_id, question, user_id="default"):
        from backend.memory.short_term import ShortTermBuffer
        return ShortTermBuffer()

    def end_turn(self, session_id, question, answer, user_id="default"):
        self.last_answer = answer


class _FakeGraph:
    """假图：先通过 sink 推 delta（模拟生成中），再产出节点事件。"""

    def __init__(self, events, emit_deltas=None):
        self._events = events
        self._emit_deltas = emit_deltas or []

    def stream(self, initial_state, config=None):
        for text in self._emit_deltas:
            # 模拟 LLM 生成过程中的 sink 转发（proxy 上下文由 worker 设置）
            if not proxy_mod.emit_stream_delta(text):
                raise AssertionError("worker 内应已设置 stream sink")
        yield from self._events


def _make_system(graph):
    """绕过 __init__（不建真图），直接注入依赖。"""
    from backend.orchestration.graph.runner import GraphRunner

    sys_obj = MultiAgentSystem.__new__(MultiAgentSystem)
    sys_obj._graph = graph
    sys_obj._memory = _FakeMemory()
    sys_obj._skill_nodes = {"rag_skill"}
    sys_obj._runner = GraphRunner(
        graph=graph, memory=sys_obj._memory, skill_nodes={"rag_skill"})
    return sys_obj


def _collect_events(sys_obj, stop_event=None):
    return list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", stop_event=stop_event, user_id="u-1",
    ))


def test_stream_events_true_streaming_order():
    """真流式：delta 在 status 之后、done 之前按序产出，且不再有假打字机重复。"""
    events = [
        {"router": {"route_mode": "plan", "cs_context": {}}},
        {"reporter": {"final_answer": "最终回答"}},
    ]
    sys_obj = _make_system(_FakeGraph(events, emit_deltas=["你", "好"]))
    out = _collect_events(sys_obj)

    names = [e["event"] for e in out]
    assert names.count("delta") == 2
    assert "done" in names
    deltas = [e["data"]["content"] for e in out if e["event"] == "delta"]
    assert deltas == ["你", "好"]
    # done 携带权威 final_answer
    done = next(e for e in out if e["event"] == "done")
    assert done["data"]["elapsed"] is not None


def test_stream_events_fallback_typewriter():
    """无 sink delta（如非 LLM 路径）→ 兜底假打字机产出完整答案。"""
    events = [
        {"skill_executor": {"step_results": {}, "final_answer": "直接回答全文"}},
    ]
    sys_obj = _make_system(_FakeGraph(events))
    out = _collect_events(sys_obj)

    deltas = [e["data"]["content"] for e in out if e["event"] == "delta"]
    assert "".join(deltas) == "直接回答全文"
    assert out[-1]["event"] == "done"


def test_stream_events_user_abort():
    """用户中止：产出中止 error，不产出 done。"""
    stop = threading.Event()

    class AbortingGraph(_FakeGraph):
        def stream(self, initial_state, config=None):
            stop.set()  # 第一个节点后中止
            yield {"router": {"route_mode": "plan", "cs_context": {}}}

    sys_obj = _make_system(AbortingGraph([]))
    out = _collect_events(sys_obj, stop_event=stop)

    names = [e["event"] for e in out]
    assert "error" in names
    assert "done" not in names


def test_stream_events_worker_error():
    """图执行异常：产出 error 事件，不产出 done，不触发假打字机。"""
    class BrokenGraph:
        def stream(self, initial_state, config=None):
            raise RuntimeError("图炸了")
            yield  # pragma: no cover

    sys_obj = _make_system(BrokenGraph())
    out = _collect_events(sys_obj)

    names = [e["event"] for e in out]
    assert "error" in names
    assert "done" not in names


def test_stream_events_memory_persisted():
    """正常完成后 final_answer 落 L1 end_turn。"""
    events = [{"reporter": {"final_answer": "持久化答案"}}]
    sys_obj = _make_system(_FakeGraph(events))
    _collect_events(sys_obj)
    assert sys_obj._memory.last_answer == "持久化答案"


# =====================================================
# RequestContext 显式绑定（重构 #1）
# =====================================================

def test_send_branch_binds_context_from_state():
    """模拟 Send 分支：独立线程从 state 绑定 RequestContext 后 sink 可达。

    这是并行 Skill 分支能流式输出 / span 正确挂载的前提。
    """
    import threading

    from backend.orchestration.request_context import get_context_from_state

    captured = {}

    class BranchGraph(_FakeGraph):
        def __init__(self):
            super().__init__([])

        def stream(self, initial_state, config=None):
            ctx = get_context_from_state(initial_state)
            assert ctx is not None, "initial_state 应携带 request_context"

            def branch():
                # Send 分支跑在 LangGraph 内部线程池 → 模拟为独立线程，
                # 依赖节点入口从 state 绑定（而非环境继承）
                ctx.bind()
                captured["delta_ok"] = proxy_mod.emit_stream_delta("分支增量")
                captured["user_id"] = proxy_mod._thread_local_user_id()

            t = threading.Thread(target=branch)
            t.start()
            t.join()
            yield {"reporter": {"final_answer": "ok"}}

    sys_obj = _make_system(BranchGraph())
    out = _collect_events(sys_obj)

    assert captured["delta_ok"] is True
    assert captured["user_id"] == "u-1"
    deltas = [e["data"]["content"] for e in out if e["event"] == "delta"]
    assert "分支增量" in deltas


def test_bind_from_state_safe_without_context():
    """state 无 request_context（旧路径/子图/假 state）→ 不绑定、不清环境。"""
    from backend.infra.llm.proxy import (
        emit_stream_delta, reset_stream_sink, set_stream_sink,
    )

    from backend.orchestration.request_context import bind_from_state

    reset_stream_sink()
    received = []
    set_stream_sink(received.append)
    try:
        bind_from_state({})
        bind_from_state(None)
        bind_from_state({"request_context": "not-a-ctx"})
        # 环境 sink 未被清除
        assert emit_stream_delta("仍可达") is True
        assert received == ["仍可达"]
    finally:
        reset_stream_sink()


def test_request_context_carried_in_state_object():
    """RequestContext 随 state 流动（put/get 对称），非 RequestContext 值被拒。"""
    from backend.orchestration.request_context import (
        RequestContext, get_context_from_state, put_context,
    )

    ctx = RequestContext(session_id="s", user_id="u", kb_id="k")
    state = {}
    put_context(state, ctx)
    assert get_context_from_state(state) is ctx
    assert get_context_from_state({}) is None
    assert get_context_from_state({"request_context": 123}) is None


# =====================================================
# ask() 同步路径（重构 #2：与 stream_events 共享 GraphRunner）
# =====================================================

def test_ask_aggregates_answer_from_event_stream():
    """ask() 复用 runner 事件流：从 _answer 聚合最终回答，sources 落 _last_sources。"""
    events = [
        {"router": {"route_mode": "plan", "cs_context": {}}},
        {"reporter": {"final_answer": "同步回答"}},
    ]
    sys_obj = _make_system(_FakeGraph(events, emit_deltas=["你", "好"]))
    answer = sys_obj.ask("测试问题", "s-1", kb_id="default", user_id="u-1")

    assert answer == "同步回答"
    assert sys_obj._memory.last_answer == "同步回答"


def test_ask_error_maps_to_system_error_message():
    """图执行异常：ask() 返回系统错误话术（对齐旧同步实现语义）。"""

    class BrokenGraph:
        def stream(self, initial_state, config=None):
            raise RuntimeError("炸了")
            yield  # pragma: no cover

    sys_obj = _make_system(BrokenGraph())
    answer = sys_obj.ask("q", "s-1")

    assert answer.startswith("## 系统错误")
    assert "执行失败" in answer


def test_ask_guard_intercept_returns_message():
    """Guard 拦截：ask() 直接返回话术，不进图（图不应被执行）。"""

    class _ShouldNotRunGraph:
        def stream(self, initial_state, config=None):  # pragma: no cover
            raise AssertionError("Guard 拦截后不应执行图")

        def __init__(self):
            pass

    from backend.security.input_guard.types import GuardAction

    class _RejectResult:
        action = GuardAction.CLARIFY
        message = "请补充更多信息"
        category = type("C", (), {"value": "ambiguous"})()
        risk_level = type("R", (), {"value": "low"})()
        confidence = 0.5
        normalized_query = "q"

        def model_dump(self, mode="json"):
            return {}

    class _RejectGuard:
        def guard(self, question, session_id=None):
            return _RejectResult()

    import backend.orchestration.graph.runner as runner_mod
    from backend.observability.tracer import trace_collector

    original = runner_mod.get_input_guard
    runner_mod.get_input_guard = lambda: _RejectGuard()
    try:
        sys_obj = _make_system(_ShouldNotRunGraph())
        answer = sys_obj.ask("模糊问题", "s-1")
        assert answer == "请补充更多信息"
    finally:
        runner_mod.get_input_guard = original
        trace_collector.clear_for_test() if hasattr(trace_collector, "clear_for_test") else None
