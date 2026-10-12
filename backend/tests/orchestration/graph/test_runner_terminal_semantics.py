"""B1-F：RequestCancelled / Timeout 终态语义收口回归（2026-10-12）

此前 runner 的 worker / 生成器两级 except Exception 把所有异常一锅端成
「执行失败: …」手写帧：用户中止被伪装成系统故障（且 cancel stage 名泄漏
进用户帧）、超时与普通 500 不可区分、生成器路径还把堆栈尾拼进 message。

本文件锁定五条语义边界（正常 / 用户取消 / 超时 / 异常 / 终帧唯一性），
并用 event_schema 契约门校验整个事件流。trace、SSE 码与 HTTP 状态的
一致性口径：
  用户取消   → SSE code=REQUEST_CANCELLED ↔ trace cancelled ↔ HTTP 499
  请求超时   → SSE code=TIMEOUT          ↔ trace error+error_code ↔ HTTP 504
  普通异常   → SSE code=INTERNAL_ERROR   ↔ trace error
工具级失败不产生终态错误（executor 已归一为 degraded 步骤结果），不在此列。
"""
import threading

import pytest

from backend.core.request_context import RequestCancelled
from backend.infra.llm import proxy as proxy_mod
from backend.observability.tracer import trace_collector
from backend.orchestration.graph.event_schema import validate_frame_sequence
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
    def __init__(self):
        self.end_turn_calls = []

    def start_session(self, session_id, question, user_id="default",
                      tenant_id=""):
        from backend.memory.short_term import ShortTermBuffer
        return ShortTermBuffer()

    def end_turn(self, session_id, question, answer, user_id="default",
                 tenant_id="", idempotency_key=""):
        self.end_turn_calls.append((session_id, question, answer))


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
    def __init__(self, events):
        self._events = events

    def stream(self, initial_state, config=None):
        yield from self._events


class _CancelledGraph:
    """模拟真实中止：置位 stop_event 后，图内取消检查边界抛 RequestCancelled。"""

    def __init__(self, stop_event):
        self._stop_event = stop_event

    def stream(self, initial_state, config=None):
        yield {"router": {"route_mode": "plan", "cs_context": {}}}
        self._stop_event.set()
        raise RequestCancelled("cs_reporter")


class _TimedOutGraph:
    def stream(self, initial_state, config=None):
        raise TimeoutError("synthetic-deadline")
        yield  # pragma: no cover


class _ExplodingGraph:
    def stream(self, initial_state, config=None):
        raise RuntimeError("boom-in-graph")
        yield  # pragma: no cover


def _events_normal():
    return [
        {"router": {"route_mode": "plan", "cs_context": {}}},
        {"reporter": {"final_answer": "最终回答"}},
    ]


def _terminal_frames(out):
    return [e for e in out if e["event"] in ("done", "error")]


def test_normal_completion_has_single_done_terminal(_spy_finish):
    sys_obj = _make_system(_StaticGraph(_events_normal()))
    out = list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=threading.Event(), idempotency_key="turn-key-1"))

    terminals = _terminal_frames(out)
    assert [t["event"] for t in terminals] == ["done"]
    validate_frame_sequence(out, require_meta=False)
    record, _ = _spy_finish.calls[0]
    assert _root(record).status == "success"


def test_user_cancel_emits_single_cancelled_terminal_frame(_spy_finish):
    """用户中止：恰好一条 REQUEST_CANCELLED 终帧；不出现「执行失败」帧、
    不泄漏 cancel stage 名、trace 终态 cancelled、不落 Memory。"""
    stop_event = threading.Event()
    sys_obj = _make_system(_CancelledGraph(stop_event))
    out = list(sys_obj.stream_events(
        "测试问题", "s-1", kb_id="default", user_id="u-1",
        stop_event=stop_event))

    terminals = _terminal_frames(out)
    assert len(terminals) == 1, [t["event"] for t in terminals]
    frame = terminals[0]
    assert frame["event"] == "error"
    assert frame["data"]["code"] == "REQUEST_CANCELLED"
    assert frame["data"]["retryable"] is False
    assert "执行失败" not in frame["data"]["message"]
    assert "cs_reporter" not in frame["data"]["message"]
    assert not any(e["event"] == "done" for e in out)
    validate_frame_sequence(out, require_meta=False)

    record, _ = _spy_finish.calls[0]
    assert _root(record).status == "cancelled"
    assert record.metadata.get("cancelled") is True
    assert sys_obj._memory.end_turn_calls == []


def test_timeout_terminal_frame_maps_to_timeout_code(_spy_finish):
    """请求/Provider 超时终态：TIMEOUT 稳定码（区别于普通 500），
    trace 根 error + error_code 归因，不落 Memory。"""
    sys_obj = _make_system(_TimedOutGraph())
    out = list(sys_obj.stream_events("测试问题", "s-1", user_id="u-1"))

    terminals = _terminal_frames(out)
    assert len(terminals) == 1
    frame = terminals[0]
    assert frame["event"] == "error"
    assert frame["data"]["code"] == "TIMEOUT"
    assert frame["data"]["retryable"] is True
    assert not any(e["event"] == "done" for e in out)
    validate_frame_sequence(out, require_meta=False)

    record, _ = _spy_finish.calls[0]
    assert _root(record).status == "error"
    assert record.metadata.get("error_code") == "TIMEOUT"
    assert sys_obj._memory.end_turn_calls == []


def test_generic_error_frame_is_protocol_envelope_without_stack(_spy_finish):
    """普通异常：协议封套出帧（code/retryable/trace_id），单行原因，
    堆栈不进 message；trace 根 error。"""
    sys_obj = _make_system(_ExplodingGraph())
    out = list(sys_obj.stream_events("测试问题", "s-1", user_id="u-1"))

    terminals = _terminal_frames(out)
    assert len(terminals) == 1
    frame = terminals[0]
    assert frame["event"] == "error"
    assert frame["data"]["code"] == "INTERNAL_ERROR"
    assert frame["data"]["message"] == "执行失败: boom-in-graph"
    assert "Traceback" not in frame["data"]["message"]
    assert "\n" not in frame["data"]["message"]
    assert not any(e["event"] == "done" for e in out)
    validate_frame_sequence(out, require_meta=False)

    record, _ = _spy_finish.calls[0]
    assert _root(record).status == "error"
    assert sys_obj._memory.end_turn_calls == []


def test_worker_timeout_trace_consistency_without_stop_event(_spy_finish):
    """无取消通道（stop_event=None）时的超时路径同样收敛 TIMEOUT 语义。"""
    sys_obj = _make_system(_TimedOutGraph())
    out = list(sys_obj.stream_events("测试问题", "s-1", user_id="u-1"))

    frame = next(e for e in out if e["event"] == "error")
    assert frame["data"]["code"] == "TIMEOUT"
    record, _ = _spy_finish.calls[0]
    assert record.metadata.get("error_code") == "TIMEOUT"
