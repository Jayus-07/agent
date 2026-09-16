"""tests/test_task_orchestration.py — 异步任务编排系统测试。

覆盖验收 8 项：
  1. 创建任务          2. 任务进入队列（eager 模拟 Worker）
  3. Worker 执行       4. 状态变化
  5. checkpoint 保存   6. 暂停恢复（WAITING_USER → resume 续跑）
  7. 失败重试          8. 多用户隔离

策略：
- 执行器语义用 stub 2 节点图 + MemorySaver（无 LLM 依赖、毫秒级）
- DB 用真实 agent_memory（容器内 PGHOST=postgres；宿主机不可达则 skip 整组）
- Celery 用 eager 模式（不需要 broker）；celery 未安装则 skip eager 用例
- 取消/暂停标志 monkeypatch task_manager（不依赖真 Redis）
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest

from backend.models.task import TaskRecord, TaskStatus


# ═══════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════

@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达则跳过整组用例。"""
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过任务编排测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def stub_graph():
    """2 节点 stub 图：step_a → step_b。

    可控点：
    - fail["active"]: step_a 抛 RuntimeError（失败重试用）
    - ask_input: step_a 返回 needs_user_input（暂停恢复用）
    - step_b 把 state["user_input"] 回显进 final_answer（验证输入注入）
    - calls: 节点调用计数（验证"恢复不重跑已完成节点"）
    """
    from langgraph.graph import END, START, StateGraph
    from langgraph.checkpoint.memory import MemorySaver

    calls = {"a": 0, "b": 0}
    fail = {"active": False}
    ask_input = {"active": False}

    class TState(TypedDict, total=False):
        question: str
        step_results: dict
        final_answer: str
        needs_user_input: dict
        user_input: str

    def step_a(state: dict) -> dict:
        calls["a"] += 1
        if fail["active"]:
            raise RuntimeError("boom: step_a failed")
        out: dict = {"step_results": {**state.get("step_results", {}), "a": "ok"}}
        if ask_input["active"]:
            out["needs_user_input"] = {"message": "请确认继续"}
        return out

    def step_b(state: dict) -> dict:
        calls["b"] += 1
        ack = f"ack:{state.get('user_input', '')}" if state.get("user_input") else "finished"
        return {"step_results": {**state.get("step_results", {}), "b": "done"},
                "final_answer": ack}

    wf = StateGraph(TState)
    wf.add_node("step_a", step_a)
    wf.add_node("step_b", step_b)
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", "step_b")
    wf.add_edge("step_b", END)
    graph = wf.compile(checkpointer=MemorySaver())
    return graph, calls, fail, ask_input


@pytest.fixture()
def task_record(pg):
    """每用例一条独立任务（uuid 用户，互不污染）。"""
    user = f"test-user-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "测试任务指令")
    yield record
    # 终态收尾不删行（保留审计），仅隔离用户命名空间


def _executor(stub_graph):
    from backend.orchestration.checkpoint import TaskGraphExecutor

    graph, _calls, _fail, _ask = stub_graph
    return TaskGraphExecutor(graph=graph, poll_control_flags=False)


def _refresh(pg, record: TaskRecord) -> TaskRecord:
    return pg.get_task(record.id)


# ═══════════════════════════════════════════════════
# 1. 创建任务
# ═══════════════════════════════════════════════════

def test_create_task_returns_pending(pg, task_record):
    assert task_record.status == TaskStatus.PENDING
    assert task_record.thread_id == f"task-{task_record.id}"
    assert task_record.input["query"] == "测试任务指令"
    assert task_record.tenant_id == "default"


# ═══════════════════════════════════════════════════
# 3 + 4. Worker 执行 / 状态变化
# ═══════════════════════════════════════════════════

def test_worker_executes_and_status_transitions(pg, task_record, stub_graph):
    output = _executor(stub_graph).execute(task_record)
    assert output["answer"] == "finished"
    record = _refresh(pg, task_record)
    assert record.status == TaskStatus.SUCCESS
    assert record.output["answer"] == "finished"
    assert record.current_node == "step_b"


# ═══════════════════════════════════════════════════
# 5. checkpoint 保存
# ═══════════════════════════════════════════════════

def test_checkpoint_rows_saved(pg, task_record, stub_graph):
    _executor(stub_graph).execute(task_record)
    cps = pg.list_checkpoints(task_record.id, task_record.user_id)
    node_names = [c["node_name"] for c in cps]
    assert "step_a" in node_names and "step_b" in node_names
    assert cps[0]["state_json"].get("step_results", {}).get("a") == "ok"


# ═══════════════════════════════════════════════════
# 6. 暂停恢复（WAITING_USER → resume 续跑，不重启整图）
# ═══════════════════════════════════════════════════

def test_waiting_user_then_resume_continues_graph(pg, task_record, stub_graph, monkeypatch):
    graph, calls, _fail, ask_input = stub_graph
    executor = _executor(stub_graph)

    ask_input["active"] = True
    result = executor.execute(task_record)
    assert result["status"] == TaskStatus.WAITING_USER.value
    record = _refresh(pg, task_record)
    assert record.status == TaskStatus.WAITING_USER
    assert calls == {"a": 1, "b": 0}          # b 未执行

    # resume API 语义：注入用户输入 + 状态回 PENDING + 重新入队（eager 环境下 stub 掉入队）
    monkeypatch.setattr("backend.tasks.task_manager.enqueue_task", lambda r: None)
    from backend.tasks import task_manager

    record = task_manager.resume_task(record.id, user_input="继续执行")
    assert record.status == TaskStatus.PENDING

    ask_input["active"] = False
    output = executor.execute(_refresh(pg, record))
    assert output["answer"] == "ack:继续执行"   # 用户输入注入成功
    assert calls == {"a": 1, "b": 1}            # step_a 未重跑（checkpoint 续跑）


# ═══════════════════════════════════════════════════
# 7. 失败重试
# ═══════════════════════════════════════════════════

def test_failure_then_retry(pg, task_record, stub_graph):
    graph, calls, fail, _ask = stub_graph
    executor = _executor(stub_graph)

    fail["active"] = True
    with pytest.raises(RuntimeError, match="boom"):
        executor.execute(task_record)
    # Celery retry 路径的落库语义（agent_tasks._fail）：FAILED + retry_count+1
    pg.update_status(task_record.id, TaskStatus.FAILED,
                     error_message="boom", progress="等待第 1 次重试")
    pg.increment_retry(task_record.id)

    fail["active"] = False
    output = executor.execute(_refresh(pg, task_record))
    assert output["answer"] == "finished"
    record = _refresh(pg, task_record)
    assert record.status == TaskStatus.SUCCESS
    assert record.retry_count == 1


def test_cancelled_task_skips_execution(pg, task_record):
    """已 CANCELLED 的任务被 Worker 拾取时直接跳过（幂等防护）。"""
    pg.update_status(task_record.id, TaskStatus.CANCELLED, error_message="用户取消")
    from backend.tasks.agent_tasks import execute_agent_task_impl

    result = execute_agent_task_impl(task_record.id)
    assert result["status"] == "CANCELLED"


# ═══════════════════════════════════════════════════
# 取消 / 暂停标志 → 任务终态（Worker 捕获路径）
# ═══════════════════════════════════════════════════

def test_cancel_flag_aborts_execution(pg, task_record, stub_graph, monkeypatch):
    monkeypatch.setattr("backend.tasks.task_manager.is_cancel_requested",
                        lambda tid: True)
    from backend.tasks.agent_tasks import execute_agent_task_impl

    result = execute_agent_task_impl(task_record.id)
    assert result["status"] == "CANCELLED"
    assert _refresh(pg, task_record).status == TaskStatus.CANCELLED


def test_pause_flag_pauses_execution(pg, task_record, stub_graph, monkeypatch):
    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested",
                        lambda tid: True)
    from backend.tasks.agent_tasks import execute_agent_task_impl

    result = execute_agent_task_impl(task_record.id)
    assert result["status"] == "PAUSED"
    assert _refresh(pg, task_record).status == TaskStatus.PAUSED


# ═══════════════════════════════════════════════════
# 8. 多用户隔离
# ═══════════════════════════════════════════════════

def test_multi_user_isolation(pg, task_record):
    stranger = f"stranger-{uuid.uuid4().hex[:8]}"
    assert pg.get_task_for_user(task_record.id, stranger) is None
    assert pg.get_task_for_user(task_record.id, task_record.user_id) is not None
    assert pg.list_tasks_for_user(stranger) == []
    assert pg.list_checkpoints(task_record.id, stranger) == []


# ═══════════════════════════════════════════════════
# 2. 任务进入队列（Celery eager 模式；无 celery 则 skip）
# ═══════════════════════════════════════════════════

def test_task_enqueued_and_executed_eager(pg, task_record, stub_graph, monkeypatch):
    celery = pytest.importorskip("celery")
    from backend.orchestration.checkpoint import task_executor as te

    graph, calls, _fail, _ask = stub_graph
    monkeypatch.setattr(te, "build_task_graph", lambda: graph)
    monkeypatch.setattr("backend.tasks.task_manager.is_cancel_requested", lambda t: False)
    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested", lambda t: False)

    from backend.tasks.agent_tasks import execute_agent_task
    from backend.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        result = execute_agent_task.apply(args=[task_record.id])
        payload = result.get()
        assert payload["status"] == "SUCCESS"
        assert _refresh(pg, task_record).status == TaskStatus.SUCCESS
    finally:
        celery_app.conf.task_always_eager = False


# ═══════════════════════════════════════════════════
# 纯单元：状态机（无外部依赖）
# ═══════════════════════════════════════════════════

def test_task_status_state_machine():
    assert TaskStatus.SUCCESS.is_terminal()
    assert not TaskStatus.RUNNING.is_terminal()
    assert set(TaskStatus.resumable()) == {
        TaskStatus.WAITING_USER, TaskStatus.PAUSED, TaskStatus.FAILED}
    assert TaskStatus("WAITING_USER") == TaskStatus.WAITING_USER
