"""make_initial_state 必须把 session_id 平铺进 state。

回归背景（2026-09-17）：make_initial_state 只平铺了 user_id/department，
漏了 session_id —— cs_prefilter state.get("session_id", "default") 恒取
兜底值，转人工工单 conversation_id 全部挤在 "default"，坐席按真实
会话 id 认领永远 404。
"""
from backend.orchestration.graph.events import make_initial_state


def test_make_initial_state_contains_session_id():
    state = make_initial_state(
        "转人工", "walkthrough-2", "default", [],
        user_id="u-1", department="",
    )
    assert state["session_id"] == "walkthrough-2"
    assert state["user_id"] == "u-1"


def test_make_initial_state_session_id_not_lost_when_empty_user():
    # user_id 为空时平铺为 ""，session_id 不受影响
    state = make_initial_state("hi", "sess-abc", "kb1", [])
    assert state["session_id"] == "sess-abc"
    assert state["user_id"] == ""
