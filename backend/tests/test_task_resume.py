"""tests/test_task_resume.py — Phase1 Task Runtime Step5 恢复语义。

停点 5 验收：
- 只允许 PAUSED（及 WAITING_USER 变体）恢复；FAILED 默认拒绝（管理端通道）
- 从最新 checkpoint 继续，已完成节点重复执行 = 0
- 重复/并发 resume 恰一个胜出：不重复入队、不产生第二个执行链
- resume 后 Worker 被 kill：租约接管续跑，最终 SUCCESS
- 入队失败回滚 PAUSED（可再次 resume）
"""
from __future__ import annotations

import threading
import uuid
from typing import TypedDict

import pytest

from backend.models.task import TaskStatus
from backend.services.task_state import TaskManager


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过恢复测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def abc3():
    """A→B→C 图（MemorySaver 单进程共享：两个 executor 实例 = 两个 Worker）。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    calls = {"step_a": 0, "step_b": 0, "step_c": 0}

    class S(TypedDict, total=False):
        step_results: dict
        final_answer: str

    def _mk(name: str):
        def _node(state: dict) -> dict:
            calls[name] += 1
            out = {"step_results": {**state.get("step_results", {}),
                                    name: "ok"}}
            if name == "step_c":
                out["final_answer"] = "done"
            return out
        return _node

    wf = StateGraph(S)
    for n in ["step_a", "step_b", "step_c"]:
        wf.add_node(n, _mk(n))
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", "step_b")
    wf.add_edge("step_b", "step_c")
    wf.add_edge("step_c", END)
    return wf.compile(checkpointer=MemorySaver()), calls


def _executor(graph):
    from backend.orchestration.checkpoint import TaskGraphExecutor

    return TaskGraphExecutor(graph=graph, poll_control_flags=False)


def _pause_to_b(pg, graph, calls, record):
    """跑到 B 完成 → TaskPaused → 落 PAUSED（复刻 Worker 捕获路径）。"""
    from backend.orchestration.checkpoint import TaskGraphExecutor, TaskPaused

    executor = TaskGraphExecutor(graph=graph, poll_control_flags=True)
    executor._paused = lambda tid: calls["step_b"] >= 1  # type: ignore
    executor._cancelled = lambda tid: False  # type: ignore
    with pytest.raises(TaskPaused):
        executor.execute(record)
    TaskManager.mark_paused(record.id)
    assert pg_status(pg, record.id) == TaskStatus.PAUSED


def pg_status(pg, task_id: str) -> TaskStatus:
    return pg.get_task(task_id).status


@pytest.fixture()
def enqueue_stub(monkeypatch):
    """入队计数桩（不依赖 broker）。"""
    counter = {"n": 0, "fail": False}

    def _fake(record):
        if counter["fail"]:
            raise ConnectionError("broker down")
        counter["n"] += 1

    monkeypatch.setattr("backend.tasks.task_manager.enqueue_task", _fake)
    return counter


# ═══════════════════════════════════════════════════
# 正常 resume：从 checkpoint 继续，C 执行一次
# ═══════════════════════════════════════════════════

def test_resume_paused_continues_from_checkpoint(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "恢复测试")
    _pause_to_b(pg, graph, calls, record)
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 0}

    from backend.tasks import task_manager

    record = task_manager.resume_task(record.id)
    assert pg_status(pg, record.id) == TaskStatus.PENDING
    assert enqueue_stub["n"] == 1

    # "另一个 Worker"（新 executor 实例，同一 checkpointer）拾取续跑
    _executor(graph).execute(pg.get_task(record.id))
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}   # A/B 未重跑
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.SUCCESS
    assert row.output["answer"] == "done"


# ═══════════════════════════════════════════════════
# 重复 / 并发 resume：不产生第二个执行链
# ═══════════════════════════════════════════════════

def test_resume_twice_sequential_no_double_enqueue(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "重复恢复")
    _pause_to_b(pg, graph, calls, record)

    from backend.tasks import task_manager

    task_manager.resume_task(record.id)
    second = task_manager.resume_task(record.id)     # 连续第二次：PENDING 幂等命中
    assert pg_status(pg, second.id) == TaskStatus.PENDING
    assert enqueue_stub["n"] == 1                    # 未重复入队


def test_resume_concurrent_two_clients_single_enqueue(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "并发恢复")
    _pause_to_b(pg, graph, calls, record)

    from backend.tasks import task_manager

    barrier = threading.Barrier(2)
    enqueued: list[int] = []
    lock = threading.Lock()

    def _client() -> None:
        barrier.wait()
        rec = task_manager.resume_task(record.id)
        # 仅胜者路径会把任务送入队列；败者幂等返回
        if rec and pg_status(pg, rec.id) == TaskStatus.PENDING:
            with lock:
                enqueued.append(enqueue_stub["n"])

    t1, t2 = threading.Thread(target=_client), threading.Thread(target=_client)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert enqueue_stub["n"] == 1                    # 恰一次入队
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 0}


# ═══════════════════════════════════════════════════
# resume 后 Worker 被 kill → 接管续跑 → SUCCESS
# ═══════════════════════════════════════════════════

def test_resume_then_worker_kill_then_takeover(pg, abc3):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "kill 恢复")
    _pause_to_b(pg, graph, calls, record)

    from backend.tasks import task_manager

    monkey_enq = record  # resume 需真实入队语义，这里桩掉 broker 后手动接管
    record = task_manager.resume_task(record.id)
    assert pg_status(pg, record.id) == TaskStatus.PENDING

    # Worker-a 拾取（租约）后立即被硬杀：无任何落库，心跳/租约停更
    # （Phase2 Step1：stale 权威 = lease_expires_at，须随 updated_at 一起回拨）
    assert pg.try_acquire_lease(record.id, worker="worker-a")
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET updated_at = now() - interval '2 hours', "
                    "lease_expires_at = now() - interval '2 hours' "
                    "WHERE id = %s", (record.id,))
    # Worker-b stale 接管续跑：A/B 不重跑，C 补跑成功
    assert pg.try_acquire_lease(record.id, worker="worker-b",
                                stale_running_seconds=1900)
    _executor(graph).execute(pg.get_task(record.id))
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.SUCCESS
    assert row.worker == "worker-b"


# ═══════════════════════════════════════════════════
# 状态门槛与失败回滚
# ═══════════════════════════════════════════════════

def test_resume_terminal_rejected(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "终态恢复拒绝")
    TaskManager.mark_running(record.id)
    TaskManager.mark_cancelled(record.id)

    from backend.tasks import task_manager

    with pytest.raises(ValueError):
        task_manager.resume_task(record.id)
    assert enqueue_stub["n"] == 0
    assert pg_status(pg, record.id) == TaskStatus.CANCELLED


def test_resume_failed_rejected_by_default_allow_admin(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "失败重试")
    TaskManager.mark_running(record.id)
    TaskManager.mark_failed(record.id, error_message="boom")

    from backend.tasks import task_manager

    with pytest.raises(ValueError):
        task_manager.resume_task(record.id)              # 默认拒绝
    assert enqueue_stub["n"] == 0
    rec = task_manager.resume_task(record.id, allow_failed=True)  # 管理端通道
    assert pg_status(pg, rec.id) == TaskStatus.PENDING
    assert enqueue_stub["n"] == 1


def test_resume_enqueue_failure_rolls_back_to_paused(pg, abc3, enqueue_stub):
    graph, calls = abc3
    record = TaskManager.create(f"resume-user-{uuid.uuid4().hex[:8]}", "入队失败")
    _pause_to_b(pg, graph, calls, record)

    from backend.tasks import task_manager

    enqueue_stub["fail"] = True
    with pytest.raises(ConnectionError):
        task_manager.resume_task(record.id)
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.PAUSED           # 回滚，可再次 resume

    enqueue_stub["fail"] = False
    task_manager.resume_task(record.id)
    assert pg_status(pg, record.id) == TaskStatus.PENDING
    assert enqueue_stub["n"] == 1


def test_resume_unknown_task(pg):
    from backend.tasks import task_manager

    with pytest.raises(LookupError):
        task_manager.resume_task(str(uuid.uuid4()))


# ═══════════════════════════════════════════════════
# 执行器路由（实机演练 2026-09-23 回归）：rag_index resume 必须回 rag_index 队列
# ═══════════════════════════════════════════════════

def test_resume_rag_index_routes_to_index_queue(pg, monkeypatch):
    from backend.services import task_service
    from backend.tasks import task_manager

    record = task_service.create_task(
        f"idx-user-{uuid.uuid4().hex[:8]}", "索引文档: r.pdf",
        graph_name="rag_index", biz_type="rag_index", biz_id="up-route-1",
        extra_input={"index_kwargs": {"upload_id": "up-route-1",
                                      "filepath": "/tmp/r.pdf",
                                      "filename": "r.pdf"}})
    TaskManager.mark_running(record.id)
    TaskManager.mark_paused(record.id)

    dispatched, enqueued = [], []
    monkeypatch.setattr("backend.tasks.index_tasks.execute_index_task.apply_async",
                        lambda *, kwargs, queue: dispatched.append((kwargs, queue))
                        or type("R", (), {"id": "celery-1"})())
    monkeypatch.setattr(task_manager, "enqueue_task",
                        lambda r: enqueued.append(r.id))
    monkeypatch.setattr(task_manager, "mark_queued",
                        lambda *a, **k: None, raising=False)

    rec = task_manager.resume_task(record.id)
    assert pg_status(pg, rec.id) == TaskStatus.PENDING
    assert len(dispatched) == 1
    kwargs, queue = dispatched[0]
    assert queue == "rag_index"
    assert kwargs["db_task_id"] == record.id
    assert kwargs["upload_id"] == "up-route-1"        # 原始 kwargs 还原
    assert enqueued == []                             # 绝不走 agent 队列


def test_agent_impl_guard_skips_rag_index_row(pg):
    from backend.tasks.agent_tasks import execute_agent_task_impl
    from backend.services import task_service

    record = task_service.create_task(
        f"idx-user-{uuid.uuid4().hex[:8]}", "索引文档: g.pdf",
        graph_name="rag_index", biz_type="rag_index", biz_id="up-guard-1",
        extra_input={"index_kwargs": {"upload_id": "up-guard-1"}})
    result = execute_agent_task_impl(record.id)
    assert result["status"] == "SKIPPED_GRAPH_MISMATCH"
    assert task_service.get_task(record.id).status == TaskStatus.PENDING  # 未被 agent 触碰
