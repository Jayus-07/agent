"""Selection Funnel 候选跨聊天轮（2026-09-23 STOP E1 回归）。

主图 thread_id 每轮唯一（checkpoint 只服务单轮恢复/interrupt），
OrchestratorState.funnel_context 不是跨轮载体。锁定新契约：

- Turn 1 漏斗成功 → 候选写入 ConversationContext（三元组主键）；
- Turn 2 新 graph state（模拟新 thread）+ 同 conversation →
  selection_decision 输入能拿到上一轮候选；
- 不同 conversation / 未运行过漏斗 → 拿不到（会话隔离）；
- 失败的漏斗 run（域图异常）不写候选（不污染下一轮）。
"""
from unittest.mock import MagicMock

import pytest

from backend.orchestration.context.conversation_context import (
    FUNNEL_CANDIDATES_MAX,
    get_conversation_context_store,
)
from backend.orchestration.graph import selection_funnel_graph_node as node_mod
from backend.orchestration.graph.direct_executor import _build_workflow_inputs
from backend.orchestration.graph.selection_funnel_graph_node import (
    selection_funnel_graph_node,
)

_SESSION = "conv-e1"


def _setup_store():
    get_conversation_context_store()._data.clear()
    yield
    get_conversation_context_store()._data.clear()


@pytest.fixture(autouse=True)
def clean_store():
    yield from _setup_store()


def _state(session_id=_SESSION, **extra):
    base = {"session_id": session_id, "user_id": "u-1", "tenant_id": "t-1",
            "question": "帮我选几款蓝牙耳机"}
    base.update(extra)
    return base


def _stub_funnel_graph(monkeypatch, top_candidates):
    """桩漏斗域图：invoke 返回带 top_candidates 的终态。"""
    final_state = {
        "candidates": top_candidates,
        "stage_logs": [{"stage": "pool", "kept": len(top_candidates),
                        "dropped": 0}],
        "brief": {},
    }
    graph = MagicMock()
    graph.invoke.return_value = final_state
    # 节点模块顶层已绑定该名字 → 在节点命名空间打桩
    monkeypatch.setattr(
        "backend.orchestration.graph.selection_funnel_graph_node."
        "get_selection_funnel_graph",
        lambda: graph)


def test_turn1_writes_turn2_reads_across_new_state(monkeypatch):
    """Turn1 漏斗成功 → Turn2 全新 state（新 thread 语义）读回候选。"""
    top = [
        {"rank": 1, "title": "耳机A", "url": "https://x/a", "rating": 4.8},
        {"rank": 2, "title": "耳机B", "url": "https://x/b", "rating": 4.6},
        {"rank": 3, "title": "耳机C", "url": "https://x/c", "rating": 4.5},
    ]
    _stub_funnel_graph(monkeypatch, top)

    # Turn 1：漏斗域图运行（写入 ConversationContext）
    out = selection_funnel_graph_node(_state())
    assert out["funnel_context"]["top"], "Turn1 应产出候选"

    # Turn 2：全新 state（无 funnel_context —— 模拟新 thread_id 新 state）
    fresh = _state(question="这几个哪个更值得做？")
    assert "funnel_context" not in fresh
    inputs = _build_workflow_inputs("selection_decision", fresh)
    got = inputs.get("funnel_candidates") or []
    titles = [c["title"] for c in got]
    assert titles == ["耳机A", "耳机B", "耳机C"]
    # 归因字段随写入补齐
    assert all(c.get("funnel_run_id") for c in got)


def test_other_conversation_cannot_read(monkeypatch):
    """会话隔离：conv-B 读不到 conv-A 的候选。"""
    _stub_funnel_graph(monkeypatch, [{"rank": 1, "title": "A1",
                                      "url": "https://x/1"}])
    selection_funnel_graph_node(_state(session_id="conv-A"))

    inputs = _build_workflow_inputs(
        "selection_decision", _state(session_id="conv-B",
                                     question="这几个哪个好？"))
    assert "funnel_candidates" not in inputs


def test_failed_funnel_run_does_not_pollute(monkeypatch):
    """域图异常（失败 run）不得写入候选——下一轮不被污染。"""
    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("boom")
    # 节点模块顶层已绑定该名字 → 在节点命名空间打桩
    monkeypatch.setattr(
        "backend.orchestration.graph.selection_funnel_graph_node."
        "get_selection_funnel_graph",
        lambda: graph)
    selection_funnel_graph_node(_state())

    inputs = _build_workflow_inputs(
        "selection_decision", _state(question="这几个哪个好？"))
    assert "funnel_candidates" not in inputs


def test_explicit_candidates_beat_conversation_cache(monkeypatch):
    """显式请求候选 > ConversationContext 缓存。"""
    set_store = get_conversation_context_store()
    ctx = set_store.get("t-1", "u-1", _SESSION)
    ctx.set_funnel_candidates([{"title": "缓存的旧候选", "url": "https://x/old"}],
                              "sel-old")

    fresh = _state(question="决策",
                   funnel_context={"top": [{"title": "本轮显式候选",
                                            "url": "https://x/new"}]})
    inputs = _build_workflow_inputs("selection_decision", fresh)
    assert [c["title"] for c in inputs["funnel_candidates"]] == ["本轮显式候选"]


def test_candidate_cap_enforced(monkeypatch):
    """Top-N 上限：超出 FUNNEL_CANDIDATES_MAX 的候选不进上下文。"""
    top = [{"rank": i + 1, "title": f"c{i}", "url": f"https://x/{i}"}
           for i in range(FUNNEL_CANDIDATES_MAX + 3)]
    _stub_funnel_graph(monkeypatch, top)
    selection_funnel_graph_node(_state())

    inputs = _build_workflow_inputs(
        "selection_decision", _state(question="哪个好？"))
    assert len(inputs["funnel_candidates"]) == FUNNEL_CANDIDATES_MAX


def test_non_decision_workflow_unaffected():
    """其他 workflow 不受兜底读取影响（契约面不变）。"""
    inputs = _build_workflow_inputs("daily_report", _state())
    assert "funnel_candidates" not in inputs
