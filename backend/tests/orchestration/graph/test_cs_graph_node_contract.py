"""test_cs_graph_node_contract.py — Main Graph ↔ CS Graph 边界契约测试

cs_graph_node 是两个 graph 之间唯一的接口，本测试锁定其三个契约：
  1. 输入构造：new_cs_graph_input 收到正确的身份/会话字段
  2. 输出映射：CSGraphResult → 主图 state 更新（含 cs_context 合并策略：
     保留 authenticated_user_id/session_id/cs_target，覆盖 conversation_id/handoff_state）
  3. 故障隔离：CS Graph 抛异常时降级兜底回复，主图不崩
  4. checkpoint：invoke config 的 thread_id == conversation_id
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def fake_graph(monkeypatch):
    """替换 get_cs_graph 为可编程 fake，捕获 invoke 入参与 config。"""
    captured: dict = {}

    class FakeGraph:
        def invoke(self, cs_input, config=None):
            captured["input"] = cs_input
            captured["config"] = config
            captured["thread_id"] = (config or {}).get("configurable", {}).get("thread_id")
            return {
                "final_answer": "已为您登记退款申请。",
                "conversation_id": "conv-new-001",
                "handoff_state": "ai_active",
                "confirmation_state": "not_required",
                "cs_audit_entries": [{"op": "refund", "ok": True}],
                "cs_action_result": {"applied": True},
                "conversation_status": "open",
            }

    from backend.orchestration.graph import cs_graph_node as mod

    monkeypatch.setattr(mod, "get_cs_graph", lambda: FakeGraph())
    return captured


BASE_STATE = {
    "question": "我的订单怎么申请退款",
    "cs_context": {
        "authenticated_user_id": "user-42",
        "session_id": "sess-abc",
        "conversation_id": "conv-old-000",
        "cs_target": "cs_business_action",
    },
}


def test_input_construction(fake_graph):
    """契约 1：主图 state → CS Graph 输入的身份字段透传正确。"""
    from backend.orchestration.graph.cs_graph_node import cs_graph_node

    cs_graph_node(dict(BASE_STATE))
    inp = fake_graph["input"]
    assert inp["user_message"] == "我的订单怎么申请退款"
    assert inp["user_id"] == "user-42"
    assert inp["session_id"] == "sess-abc"
    assert inp["cs_route"] == {}


def test_output_mapping_and_context_merge(fake_graph):
    """契约 2：CS 结果映射回主图；cs_context 合并遵循保留/覆盖策略。"""
    from backend.orchestration.graph.cs_graph_node import cs_graph_node

    update = cs_graph_node(dict(BASE_STATE))
    assert update["final_answer"] == "已为您登记退款申请。"
    assert update["cs_action_result"] == {"applied": True}
    assert update["cs_audit_entries"] == [{"op": "refund", "ok": True}]

    ctx = update["cs_context"]
    # 保留：来自原 state
    assert ctx["authenticated_user_id"] == "user-42"
    assert ctx["session_id"] == "sess-abc"
    assert ctx["cs_target"] == "cs_business_action"
    # 覆盖：来自 CS Graph 结果
    assert ctx.get("conversation_id") == "conv-new-001"
    assert ctx.get("handoff_state") == "ai_active"


def test_thread_id_is_conversation_id(fake_graph):
    """契约 4：checkpointer thread_id 必须用 conversation_id（会话恢复前提）。"""
    from backend.orchestration.graph.cs_graph_node import cs_graph_node

    cs_graph_node(dict(BASE_STATE))
    assert fake_graph["thread_id"] == "conv-old-000"


def test_exception_falls_back_safely(monkeypatch):
    """契约 3：CS Graph 异常 → 兜底回复，主图不崩，保留身份字段。"""
    from backend.orchestration.graph import cs_graph_node as mod

    class BrokenGraph:
        def invoke(self, cs_input, config=None):
            raise RuntimeError("cs graph down")

    monkeypatch.setattr(mod, "get_cs_graph", lambda: BrokenGraph())
    update = mod.cs_graph_node(dict(BASE_STATE))
    assert update["final_answer"]
    assert "抱歉" in update["final_answer"] or "稍后" in update["final_answer"]
    assert update["cs_context"]["authenticated_user_id"] == "user-42"
    assert update["cs_context"]["session_id"] == "sess-abc"
    assert update["cs_context"]["cs_target"] == "cs_business_action"
