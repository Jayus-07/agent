"""tests/test_task_cancel.py — Phase1 Task Runtime Step6 取消语义。

停点 6 验收：
- RUNNING → cancel：后续节点执行次数 = 0，Worker 捕获落 CANCELLED
- PAUSED / PENDING → cancel：DB 直接落 CANCELLED（不经 Worker）
- 重复 cancel 幂等；cancel 后 resume 被明确拒绝
- checkpoint 与已完成结果保留
"""
from __future__ import annotations

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
        pytest.skip(f"agent_memory 不可达，跳过取消测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def abc3():
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


def _new_task(pg, query: str = "取消测试"):
    return TaskManager.create(f"cancel-user-{uuid.uuid4().hex[:8]}", query)


# ═══════════════════════════════════════════════════
# RUNNING → cancel：后续节点执行次数 = 0
# ═══════════════════════════════════════════════════

def test_running_cancel_stops_next_node(pg, abc3):
    graph, calls = abc3
    record = _new_task(pg)

    from backend.orchestration.checkpoint import TaskCancelled, TaskGraphExecutor

    executor = TaskGraphExecutor(graph=graph, poll_control_flags=True)
    executor._cancelled = lambda tid: calls["step_a"] >= 1  # type: ignore
    executor._paused = lambda tid: False  # type: ignore

    with pytest.raises(TaskCancelled):
        executor.execute(record)
    assert calls == {"step_a": 1, "step_b": 0, "step_c": 0}   # 后续节点 0 次

    # Worker 捕获路径落 CANCELLED（复刻 impl._fail）
    TaskManager.mark_cancelled(record.id)
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.CANCELLED
    assert row.current_node == "step_a"                       # checkpoint/进度保留
    cps = pg.list_checkpoints(record.id, record.user_id)
    assert [c["node_name"] for c in cps] == ["step_a"]        # 已完成结果保留


def test_cancel_running_via_manager_sets_flag(pg, monkeypatch):
    """RUNNING 走标志路径：置标志成功即 ok（Worker 边界生效）。"""
    from backend.tasks import task_manager

    flag = {"set": False}
    monkeypatch.setattr(task_manager, "request_cancel",
                        lambda tid: flag.__setitem__("set", True) or True)
    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    result = task_manager.cancel_task(record.id)
    assert result == {"ok": True, "already": False,
                      "status": "RUNNING", "mode": "flag"}
    assert flag["set"]


# ═══════════════════════════════════════════════════
# PAUSED / PENDING → cancel：不经 Worker 直接落 CANCELLED
# ═══════════════════════════════════════════════════

def test_cancel_paused_lands_cancelled(pg):
    from backend.tasks import task_manager

    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    TaskManager.mark_paused(record.id)

    result = task_manager.cancel_task(record.id)
    assert result == {"ok": True, "already": False,
                      "status": "CANCELLED", "mode": "db"}
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.CANCELLED
    assert row.finished_at is not None
    # 取消后 Worker 不能再认领（后续节点永不开始）
    assert pg.try_acquire_lease(record.id, worker="worker-a") is None


def test_cancel_pending_lands_cancelled_with_revoke(pg, monkeypatch):
    from backend.tasks import task_manager

    revoked: list[str] = []
    monkeypatch.setattr(task_manager, "_revoke_queued",
                        lambda tid: revoked.append(tid))
    record = _new_task(pg)
    result = task_manager.cancel_task(record.id)
    assert result["mode"] == "db" and result["status"] == "CANCELLED"
    assert revoked == [record.id]
    assert pg.get_task(record.id).status == TaskStatus.CANCELLED


def test_cancel_waiting_user_lands_cancelled(pg):
    from backend.tasks import task_manager

    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    pg.update_status(record.id, TaskStatus.WAITING_USER, progress="等人")
    result = task_manager.cancel_task(record.id)
    assert result["mode"] == "db"
    assert pg.get_task(record.id).status == TaskStatus.CANCELLED


# ═══════════════════════════════════════════════════
# 幂等 / resume 拒绝 / 未知任务
# ═══════════════════════════════════════════════════

def test_repeated_cancel_idempotent(pg):
    from backend.tasks import task_manager

    record = _new_task(pg)
    first = task_manager.cancel_task(record.id)
    second = task_manager.cancel_task(record.id)     # 重复 cancel 幂等
    assert first["ok"] and second == {"ok": True, "already": True,
                                      "status": "CANCELLED"}
    assert pg.get_task(record.id).status == TaskStatus.CANCELLED


def test_cancel_then_resume_rejected(pg, monkeypatch):
    from backend.tasks import task_manager

    enqueued: list[str] = []
    monkeypatch.setattr(task_manager, "enqueue_task",
                        lambda r: enqueued.append(r.id))
    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    TaskManager.mark_paused(record.id)
    task_manager.cancel_task(record.id)

    with pytest.raises(ValueError):
        task_manager.resume_task(record.id)          # Cancel 后禁止 Resume
    assert enqueued == []
    assert pg.get_task(record.id).status == TaskStatus.CANCELLED


def test_cancel_success_task_reports_already(pg):
    from backend.tasks import task_manager

    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    TaskManager.mark_success(record.id)
    result = task_manager.cancel_task(record.id)
    assert result == {"ok": True, "already": True, "status": "SUCCESS"}


def test_cancel_unknown_task(pg):
    from backend.tasks import task_manager

    with pytest.raises(LookupError):
        task_manager.cancel_task(str(uuid.uuid4()))
