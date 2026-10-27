"""用真实 LangGraph 验证计划移交、历史保留及已完成节点不重跑。"""
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.orchestration.checkpoint.chat_handoff import (
    is_persistent_plan, seed_task, stream_with_handoff,
)


class State(TypedDict, total=False):
    question: str
    plan: dict
    messages: list
    step_results: dict
    final_answer: str


@pytest.mark.parametrize("capabilities,expected", [
    ([], False), (["general_chat"], False), (["report.generate"], False),
    (["rag.search"], False), (["sql.query", "report.generate"], True),
    (["rag.search", "web.search"], True),
])
def test_policy_uses_business_plan(capabilities, expected):
    state = {"question": "写30000字、50个段落的深度分析报告",
             "plan": {"nodes": {str(i): {"capability": cap}
                                for i, cap in enumerate(capabilities)}}}
    assert is_persistent_plan(state) is expected


def test_handoff_keeps_context_and_resumes_without_replanning(monkeypatch):
    from backend.orchestration.checkpoint import task_executor
    from backend.services import task_service

    calls = []
    graph = StateGraph(State)

    def planner(state):
        calls.append("planner")
        return {"plan": {"nodes": {"a": {"capability": "sql.query"},
                                   "b": {"capability": "report.generate"}}}}

    def critique(state):
        calls.append("critique")
        return {"plan": state["plan"]}

    def business(state):
        calls.append("business")
        assert state["messages"] == [{"role": "user", "content": "只看华东"}]
        assert state["step_results"]["completed"]["output"] == "已确认时间范围"
        return {"final_answer": "华东报告"}

    for name, fn in (("planner", planner), ("critique", critique),
                     ("business", business)):
        graph.add_node(name, fn)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "critique")
    graph.add_edge("critique", "business")
    graph.add_edge("business", END)
    foreground = graph.compile()
    background = graph.compile(checkpointer=MemorySaver())
    monkeypatch.setattr(task_executor, "build_task_graph", lambda: background)
    writes = []
    monkeypatch.setattr(task_service, "append_checkpoint",
                        lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(task_service, "update_progress", lambda *a, **kw: None)

    def handoff(state, node):
        seed_task("test", state, node)
        return {"task_id": "test"}

    result = list(stream_with_handoff(foreground, {
        "question": "分析并生成报告",
        "messages": [{"role": "user", "content": "只看华东"}],
        "step_results": {"completed": {"output": "已确认时间范围"}},
    }, {}, handoff))
    assert result[-1] == {"__task_handoff": {"task_id": "test"}}
    assert calls == ["planner", "critique"]
    config = {"configurable": {"thread_id": "task-test"}}
    assert background.get_state(config).next == ("business",)
    assert background.invoke(None, config)["final_answer"] == "华东报告"
    assert calls == ["planner", "critique", "business"]
    assert writes[0][0][1] == "critique"


def test_single_generation_never_calls_handoff():
    graph = StateGraph(State)
    graph.add_node("general_chat", lambda state: {"final_answer": "长正文"})
    graph.add_edge(START, "general_chat")
    graph.add_edge("general_chat", END)

    def forbidden(*args):
        pytest.fail("单次生成不能创建持久任务")

    events = list(stream_with_handoff(graph.compile(), {
        "question": "写一万字的讲义", "plan": {"nodes": {}},
    }, {}, forbidden))
    assert events == [{"general_chat": {"final_answer": "长正文"}}]
