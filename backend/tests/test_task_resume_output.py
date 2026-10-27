"""恢复执行必须保留检查点中的累计结果，不能重跑已完成节点。"""
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.models.task import TaskRecord, TaskStatus
from backend.orchestration.checkpoint.task_executor import TaskGraphExecutor


class ResumeState(TypedDict, total=False):
    request_context: dict
    final_answer: str
    step_results: dict
    user_input: str


@pytest.mark.parametrize("finished", [True, False])
def test_resume_preserves_checkpoint_output(monkeypatch, finished):
    from backend.services import task_service

    calls = []
    fresh_context = {"user_id": "42", "permissions": ["current"]}

    def answer(state):
        calls.append("answer")
        return {"final_answer": "已保存的回答", "step_results": {"first": "ok"}}

    def tail(state):
        calls.append("tail")
        assert state["request_context"] == fresh_context
        # 后续节点仅返回增量，既有结果仍须保留。
        return {"step_results": {"second": "ok"}}

    builder = StateGraph(ResumeState)
    builder.add_node("answer", answer)
    builder.add_node("tail", tail)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END if finished else "tail")
    builder.add_edge("tail", END)
    graph = builder.compile(
        checkpointer=MemorySaver(), interrupt_after=[] if finished else ["answer"]
    )
    record = TaskRecord(id="resume-output", user_id="42", thread_id="resume-output")
    config = {"configurable": {"thread_id": record.thread_id}}
    graph.invoke({"request_context": {"permissions": ["stale"]}}, config)
    assert calls == ["answer"]
    statuses = []
    monkeypatch.setattr(task_service, "list_checkpoints", lambda *a: [{}])
    monkeypatch.setattr(task_service, "get_user_input", lambda *a: "")
    monkeypatch.setattr(task_service, "update_status", lambda *a, **kw: statuses.append((a[1], kw)))
    monkeypatch.setattr(task_service, "update_progress", lambda *a, **kw: True)
    monkeypatch.setattr(task_service, "append_checkpoint", lambda *a, **kw: True)
    executor = TaskGraphExecutor(graph=graph, poll_control_flags=False)
    monkeypatch.setattr(executor, "_build_request_context", lambda r: fresh_context)
    monkeypatch.setattr(executor, "_publish", lambda *a, **kw: None)

    output = executor._execute_inner(record)

    assert output["answer"] == "已保存的回答"
    expected = {"first": "ok"} if finished else {"first": "ok", "second": "ok"}
    assert output["step_results"] == expected
    assert calls == (["answer"] if finished else ["answer", "tail"])
    assert statuses[-1] == (TaskStatus.SUCCESS, {
        "error_message": "", "progress": "执行完成", "output": output,
        "execution_id": None,
    })
    assert graph.get_state(config).values["request_context"] == fresh_context
