"""test_sse_event_schema.py — SSE 事件契约校验门（P2-1 阶段一）。

三层验证：
  1) 单元：13 种事件合法样例逐帧过契约；非法样例（未知 event/必填缺失/
     类型错误/非 dict）必须抛
  2) 帧序：meta 首帧唯一、done/error 终帧唯一且最后、ping 不计入、
     runner 层（require_meta=False）放行
  3) 采集：a) 假图驱动 MultiAgentSystem.stream_events 的真实事件流
     逐帧过契约（runner 层口径）；b) HTTP 层 _EchoAgent 完整 SSE 流
     （meta → delta → done）过完整帧序——改 events.py/chat.py 帧构造的人
     破坏契约立即被这里拦截
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.orchestration.graph.event_schema import (
    KNOWN_EVENTS,
    validate_frame,
    validate_frame_sequence,
)

_T = 1700000000.0


def _frame(event: str, **data) -> dict:
    return {"event": event, "data": {"ts": _T, **data}}


# ── 1) 单帧契约 ──────────────────────────────────────


@pytest.mark.parametrize("frame", [
    {"event": "meta", "data": {"request_id": "r1", "node_labels": {}, "ts": _T}},
    _frame("status", node="router"),
    _frame("log", node="planner", message="派发: x", step_id="1", level="info"),
    _frame("delta", content="你"),
    _frame("done", elapsed=1.2, sources=[]),
    _frame("error", message="失败"),
    _frame("todo", items=[{"step_id": "1"}]),
    _frame("usage", total_tokens=10),
    _frame("file", node="rag_skill", files=["a.csv"]),
    _frame("clarification", question="去哪?", options=[]),
    {"event": "context", "data": {"kind": "l2_trimmed", "before": 10}},
    _frame("thinking", content="推理中"),
    # 多域隔离 M1（2026-10-06）：域引导交接卡（v + target_domain 必填）
    _frame("handoff", v=1, target_domain="travel"),
    _frame("ping"),
])
def test_all_known_event_samples_pass(frame):
    validate_frame(frame)


def test_unknown_event_rejected():
    with pytest.raises(ValueError, match="未知事件类型"):
        validate_frame({"event": "ghost_event", "data": {}})


def test_missing_required_field_rejected():
    with pytest.raises(Exception):
        validate_frame({"event": "meta", "data": {"node_labels": {}}})  # 缺 request_id
    with pytest.raises(Exception):
        validate_frame({"event": "delta", "data": {"ts": _T}})  # 缺 content
    with pytest.raises(Exception):
        validate_frame({"event": "error", "data": {"ts": _T}})  # 缺 message


def test_wrong_type_rejected():
    with pytest.raises(Exception):
        validate_frame({"event": "delta", "data": {"content": 123, "ts": _T}})
    with pytest.raises(Exception):
        validate_frame({"event": "done", "data": {"elapsed": "fast", "sources": []}})


def test_non_dict_frame_rejected():
    with pytest.raises(ValueError, match="必须是 dict"):
        validate_frame("not-a-frame")


def test_known_events_complete():
    """契约登记完备性：核心六类 + 辅助七类（含 handoff，多域隔离 M1）+ ping。"""
    assert {"meta", "status", "log", "delta", "done", "error"} <= KNOWN_EVENTS
    assert {"todo", "usage", "file", "clarification", "context", "thinking",
            "handoff"} <= KNOWN_EVENTS
    assert "ping" in KNOWN_EVENTS
    assert len(KNOWN_EVENTS) == 14


# ── 2) 帧序约束 ──────────────────────────────────────


def _full_stream() -> list[dict]:
    return [
        {"event": "meta", "data": {"request_id": "r1", "node_labels": {}, "ts": _T}},
        _frame("status", node="router"),
        _frame("log", node="planner", message="m"),
        _frame("delta", content="答"),
        _frame("done", elapsed=1.0, sources=[]),
    ]


def test_valid_sequence_passes():
    out = validate_frame_sequence(_full_stream())
    assert [f["event"] for f in out] == ["meta", "status", "log", "delta", "done"]


def test_handoff_aux_midstream_allowed():
    """handoff 是 AUX 帧：中段任意位置任意次出现合法（多域隔离 M1）。"""
    frames = _full_stream()
    frames.insert(3, _frame("handoff", v=1, target_domain="travel"))
    frames.insert(4, _frame("handoff", v=1, target_domain="customer_service"))
    out = validate_frame_sequence(frames)
    assert [f["event"] for f in out][:5] == [
        "meta", "status", "log", "handoff", "handoff"]
    assert out[-1]["event"] == "done"


def test_handoff_missing_required_rejected():
    with pytest.raises(Exception):
        validate_frame({"event": "handoff", "data": {"v": 1, "ts": _T}})  # 缺 target_domain


def test_ping_ignored_in_sequence():
    frames = _full_stream()
    frames.insert(2, _frame("ping"))
    out = validate_frame_sequence(frames)
    assert "ping" not in [f["event"] for f in out]


def test_missing_meta_first_rejected():
    frames = _full_stream()[1:]
    with pytest.raises(ValueError, match="首帧必须是 meta"):
        validate_frame_sequence(frames)


def test_duplicate_meta_rejected():
    frames = _full_stream()
    frames.insert(1, frames[0])
    with pytest.raises(ValueError, match="meta 帧必须唯一"):
        validate_frame_sequence(frames)


def test_frames_after_terminal_rejected():
    frames = _full_stream()
    frames.append(_frame("status", node="late"))  # 终帧之后再出帧
    with pytest.raises(ValueError, match="终帧 .* 之后仍有"):
        validate_frame_sequence(frames)


def test_double_terminal_rejected():
    frames = _full_stream()
    frames.append(_frame("error", message="x"))
    with pytest.raises(ValueError, match="终帧.*必须唯一"):
        validate_frame_sequence(frames)


def test_missing_terminal_rejected():
    frames = _full_stream()[:-1]
    with pytest.raises(ValueError, match="缺少终帧"):
        validate_frame_sequence(frames)


def test_runner_level_stream_without_meta_allowed():
    """runner 层事件流无 meta（chat.py 注入）：require_meta=False 放行且不要求终帧。"""
    frames = [
        _frame("status", node="router"),
        _frame("delta", content="a"),
    ]
    validate_frame_sequence(frames, require_meta=False)


# ── 3a) runner 层真实事件流采集 ───────────────────────


@pytest.fixture(autouse=True)
def _fake_guard(monkeypatch):
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
    def start_session(self, session_id, question, user_id="default", tenant_id=""):
        from backend.memory.short_term import ShortTermBuffer
        return ShortTermBuffer()

    def end_turn(self, session_id, question, answer, user_id="default", tenant_id=""):
        self.last_answer = answer


class _FakeGraph:
    def __init__(self, events, emit_deltas=None):
        self._events = events
        self._emit_deltas = emit_deltas or []

    def stream(self, initial_state, config=None):
        yield from self._events


def test_runner_event_stream_passes_contract():
    """假图驱动 runner 完整链路：产出事件逐帧过契约（require_meta=False）。"""
    from backend.infra.llm import proxy as proxy_mod
    from backend.orchestration.graph.runner import GraphRunner
    from backend.orchestration.graph.system import MultiAgentSystem

    proxy_mod.reset_stream_sink()
    graph = _FakeGraph([
        {"router": {"route_mode": "plan", "cs_context": {}}},
        {"reporter": {"final_answer": "最终回答"}},
    ])
    sys_obj = MultiAgentSystem.__new__(MultiAgentSystem)
    sys_obj._graph = graph
    sys_obj._memory = _FakeMemory()
    sys_obj._skill_nodes = {"rag_skill"}
    sys_obj._runner = GraphRunner(
        graph=graph, memory=sys_obj._memory, skill_nodes={"rag_skill"})
    events = list(sys_obj.stream_events(
        "测试问题", "sch-1", kb_id="default", user_id="u-1",
    ))
    assert events, "假图应产出事件"
    # runner 层口径：无 meta、有终帧 done
    validate_frame_sequence(events, require_meta=False)
    proxy_mod.reset_stream_sink()


# ── 3b) HTTP 层完整 SSE 流 ──────────────────────────


class _EchoAgent:
    def stream_events(self, question, session_id, **kwargs):
        yield {"event": "delta", "data": {"content": "ok", "ts": _T}}
        yield {"event": "done", "data": {"elapsed": 0.0, "sources": []}}


def test_http_full_sse_stream_passes_sequence():
    """_EchoAgent 完整 HTTP 流（meta 首帧 → delta → done 终帧）过完整帧序。"""
    import json as _json

    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.chat as chat_mod

    chat_mod.get_multi_agent = lambda: _EchoAgent()
    auth_mw.API_KEY = "test"
    from backend.app.server import app

    client = TestClient(app)
    with client.stream(
        "POST", "/chat/stream",
        json={"question": "hi", "session_id": "schema-test"},
        headers={"X-API-Key": "test"},
    ) as resp:
        body = b"".join(resp.iter_raw()).decode("utf-8", errors="replace")

    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        evt_name = data_line = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                evt_name = line[len("event: "):].strip()
            elif line.startswith("data: "):
                data_line = line[len("data: "):]
        assert evt_name, f"帧缺少 event 行: {block[:60]!r}"
        frames.append({"event": evt_name, "data": _json.loads(data_line or "{}")})

    assert frames, "应采集到帧"
    validate_frame_sequence(frames)  # 含 meta 首帧 + done 终帧的完整口径
