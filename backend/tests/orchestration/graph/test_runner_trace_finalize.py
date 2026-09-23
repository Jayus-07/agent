"""stream_events 各退出路径的 trace 收尾单测（2026-09-23 P0-3 回归）

此前「生成器被 close（客户端断连 / 用户中止后前端停止拉流）」路径不执行
任何收尾：该轮 trace 永不 finish、root span 泄漏在内存 collector 里。
本文件锁定：正常完成 / 用户中止 / generator.close() / 内部异常 四条路径
下，每轮 trace 恰好 finish 一次且 root span 关闭、状态与原因可辨。

用假 graph 驱动 MultiAgentSystem.stream_events 的 worker 线程 + 合并队列
架构（同 test_stream_events_flow.py 的 harness）；finish 打桩避免触真
SQLite 存储，_end_root 走真实 collector 以验证 root span 确实关闭。
"""
import pytest

from backend.infra.llm import proxy as proxy_mod
from backend.observability.tracer import trace_collector
from backend.orchestration.graph.runner import GraphRunner
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


class _FinishSpy:
    """trace_collector.finish 替身：记录 (record, answer)，不触真存储。"""

    def __init__(self):
        self.calls: list = []

    def __call__(self, record, answer, total_ms, model, provider=""):
        self.calls.append((record, answer))


@pytest.fixture(autouse=True)
def _spy_finish(monkeypatch):
    spy = _FinishSpy()
    monkeypatch.setattr(trace_collector, "finish", spy)
    return spy


class _FakeMemory:
    def start_session(self, session_id, question, user_id="default",
                      tenant_id=""):
        from backend.memory.short_term import ShortTermBuffer
        return ShortTermBuffer()

    def end_turn(self, session_id, question, answer, user_id="default",
                 tenant_id=""):
        self.last_answer = answer


def _root(record):
    return next(sp for sp in record.spans if sp.parent_id is None)


def _make_system(graph):
    """绕过 __init__（不建真图），直接注入依赖。"""
    sys_obj = MultiAgentSystem.__new__(MultiAgentSystem)
    sys_obj._graph = graph
    sys_obj._memory = _FakeMemory()
    sys_obj._skill_nodes = {"rag_skill"}
    sys_obj._runner = GraphRunner(
        graph=graph, memory=sys_obj._memory, skill_nodes={"rag_skill"})
    return sys_obj


class _StaticGraph:
    """按序产出节点事件的假图。"""

    def __init__(self, events):
        self._events = events

    def stream(self, initial_state, config=None):
        yield from self._events


class _AbortingGraph:
    """产出第一个事件后置位 stop_event，模拟用户在流式中途点停止。"""

    def __init__(self, events, stop_event):
        self._events = events
        self._stop_event = stop_event

    def stream(self, initial_state, config=None):
        for i, evt in enumerate(self._events):
            yield evt
            if i == 0:
                self._stop_event.set()


class _ExplodingGraph:
    """产出第一个事件后抛异常，模拟图执行内部错误。"""

    def __init__(self, events):
        self._events = events

    def stream(self, initial_state, config=None):
        for evt in self._events:
            yield evt
        raise RuntimeError("boom-in-graph")


def _events_normal():
    return [
        {"router": {"route_mode": "plan", "cs_context": {}}},
        {"reporter": {"final_answer": "最终回答"}},
    ]


def test_normal_completion_finishes_once_with_answer(_spy_finish):
    import threading
    sys_obj = _make_system(_StaticGraph(_events_normal()))
    out = list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=threading.Event()))

    names = [e["event"] for e in out]
    assert "done" in names
    # 恰好 finish 一次，answer 为最终回答
    assert len(_spy_finish.calls) == 1
    record, answer = _spy_finish.calls[0]
    assert answer == "最终回答"
    root = _root(record)
    assert root.end_time != ""
    assert root.status == "success"


def test_user_abort_mid_stream_finishes_once_with_reason(_spy_finish):
    import threading
    stop_event = threading.Event()
    sys_obj = _make_system(_AbortingGraph(_events_normal(), stop_event))
    out = list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=stop_event))

    names = [e["event"] for e in out]
    # 中止路径：有 error 帧、无 done 帧
    assert "error" in names
    assert "done" not in names
    assert len(_spy_finish.calls) == 1
    record, _answer = _spy_finish.calls[0]
    root = _root(record)
    assert root.end_time != ""
    assert root.status == "error"
    assert root.metrics.get("reason") == "user_abort"


def test_generator_close_finishes_once_as_client_disconnect(_spy_finish):
    """断连等价路径：消费方 close() 生成器 → GeneratorExit → 恰好收尾一次。

    修复前：close 后 finally 只管 end_turn，trace 永不 finish（P0-3 本体）。
    """
    import threading
    sys_obj = _make_system(_StaticGraph(_events_normal()))
    gen = sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=threading.Event())

    first = next(gen)          # 消费一个事件后挂起在 yield 处
    assert first["event"] in ("status", "delta")
    gen.close()                # GeneratorExit 在挂起点抛出

    assert len(_spy_finish.calls) == 1
    record, _answer = _spy_finish.calls[0]
    root = _root(record)
    assert root.end_time != ""
    assert root.status == "error"
    # stop_event 未置位 → 归因为客户端断连，而非用户中止
    assert root.metrics.get("reason") == "client_disconnect"


def test_generator_close_after_abort_reports_user_abort(_spy_finish):
    """stop 已置位后再 close → 归因 user_abort（中止在前，断连只是收尾方式）。"""
    import threading
    stop_event = threading.Event()
    sys_obj = _make_system(_AbortingGraph(_events_normal(), stop_event))
    gen = sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=stop_event)

    next(gen)
    stop_event.set()           # 模拟先 /chat/abort
    gen.close()

    assert len(_spy_finish.calls) == 1
    record, _answer = _spy_finish.calls[0]
    assert _root(record).metrics.get("reason") == "user_abort"


def test_worker_exception_finishes_once_with_error(_spy_finish):
    sys_obj = _make_system(_ExplodingGraph(_events_normal()))
    out = list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1"))

    names = [e["event"] for e in out]
    assert "error" in names
    assert len(_spy_finish.calls) == 1
    record, _answer = _spy_finish.calls[0]
    root = _root(record)
    assert root.end_time != ""
    assert root.status == "error"
    assert root.metrics.get("error") == "graph_failed"


def test_double_close_does_not_finish_twice(_spy_finish):
    """幂等：收尾后再次 close 不产生第二次 finish（哨兵竞态防御）。"""
    import threading
    sys_obj = _make_system(_StaticGraph(_events_normal()))
    gen = sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=threading.Event())

    next(gen)
    gen.close()
    try:
        gen.close()            # 第二次 close 是 no-op，但防御性锁定语义
    except StopIteration:
        pass

    assert len(_spy_finish.calls) == 1
