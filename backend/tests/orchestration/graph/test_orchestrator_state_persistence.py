"""OrchestratorState 域上下文持久化单测（2026-09-23 P1-1/P1-2 回归）

LangGraph 会把节点返回中未在 state schema 声明的键在 checkpoint 写入时
静默剥离（pregel/_algo：仅 warning 后丢弃）。funnel_context / travel_context
此前未登记 → 漏斗产出与旅游路由元数据写入即丢。本文件用真实
OrchestratorState schema + checkpointer 的两轮 invoke 锁定：

- 登记字段同轮节点间可见、且跨轮（同 thread checkpoint）存活；
- 未登记键仍被剥离（对照组，证明测试真实覆盖剥离语义，防假阳性）。
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.orchestration.state import OrchestratorState

_FUNNEL = {"conversation_id": "c-1", "source": "prefilter",
           "top": [{"id": "A"}, {"id": "B"}]}
_TRAVEL = {"conversation_id": "c-1",
           "travel_route": {"source": "prefilter"}}


def _make_two_node_graph():
    """writer 写入域上下文 + 未登记的对照键；reader 在下一超步读回。"""
    captured: dict = {}

    def writer(state: dict) -> dict:
        return {
            "funnel_context": dict(_FUNNEL),
            "travel_context": dict(_TRAVEL),
            "rogue_unregistered": {"should": "be-stripped"},
        }

    def reader(state: dict) -> dict:
        captured["funnel_context"] = state.get("funnel_context")
        captured["travel_context"] = state.get("travel_context")
        captured["rogue_unregistered"] = state.get("rogue_unregistered")
        return {}

    wf = StateGraph(OrchestratorState)
    wf.add_node("writer", writer)
    wf.add_node("reader", reader)
    wf.add_edge(START, "writer")
    wf.add_edge("writer", "reader")
    wf.add_edge("reader", END)
    return wf.compile(checkpointer=MemorySaver()), captured


def test_registered_contexts_survive_superstep_and_next_turn():
    graph, captured = _make_two_node_graph()
    config = {"configurable": {"thread_id": "t-orch-state-1"}}

    # Turn 1：writer 写入 → reader（下一超步）读回
    graph.invoke({"question": "turn1"}, config=config)
    assert captured["funnel_context"] == _FUNNEL
    assert captured["travel_context"] == _TRAVEL
    # 对照组：未登记键同轮即被剥离（剥离语义真实生效，测试非永真）
    assert captured["rogue_unregistered"] is None

    # Turn 2：同 thread 从 checkpoint 恢复，登记字段必须仍在
    graph.invoke({"question": "turn2"}, config=config)
    assert captured["funnel_context"]["top"] == _FUNNEL["top"]
    assert captured["travel_context"]["conversation_id"] == "c-1"
    assert captured["rogue_unregistered"] is None


def test_context_fields_declared_in_schema():
    """静态防线：字段一旦被移出 schema 立即红（selection_blocked 同款口径）。"""
    assert "funnel_context" in OrchestratorState.__annotations__
    assert "travel_context" in OrchestratorState.__annotations__
