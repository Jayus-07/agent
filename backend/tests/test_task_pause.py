"""tests/test_task_pause.py — Phase1 Task Runtime Step4 暂停语义。

停点 4 验收：
- RUNNING 中请求 pause：当前节点完成（不强行中断原子节点），下一节点执行次数 = 0
- task.status = PAUSED（Worker 捕获 TaskPaused 落库）
- checkpoint 指向最后成功节点（current_node/checkpoint_id 为最后完成节点的真实值）
- 重复 pause 幂等；PENDING（未拾取）直接队列内暂停，不依赖 Worker；终态拒绝
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest

from backend.models.task import TaskStatus


# ── STOP D P0（2026-09-23）：TaskGraphExecutor 执行时解析授权 ──
# 本文件测取消/暂停/租约接管语义，不是授权本身；授权解析整体替换为
# 合法 editor 上下文（执行器注入/刷新逻辑仍真实执行）。
@pytest.fixture(autouse=True)
def _task_auth_enabled(monkeypatch):
    import backend.security.task_authorization as _ta
    from backend.security.authorization import build_tool_authorization_context

    ctx = build_tool_authorization_context(
        user_id="900001", department="ecom", tenant_id="default",
        roles=("editor",))
    monkeypatch.setattr(_ta, "resolve_task_authorization",
                        lambda uid, tid: ctx)
from backend.services.task_state import TaskManager


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过暂停测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def abc3():
    """A→B→C 三节点图（MemorySaver，单进程暂停语义足够）。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    calls = {"step_a": 0, "step_b": 0, "step_c": 0}

    class S(TypedDict, total=False):
        question: str
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
    graph = wf.compile(checkpointer=MemorySaver())
    return graph, calls


def _run_impl(pg, record_id: str):
    """经 impl 执行（含租约与 TaskPaused 捕获落库），Redis 标志由桩控制。"""
    from backend.tasks.agent_tasks import execute_agent_task_impl

    return execute_agent_task_impl(record_id)


@pytest.fixture()
def pause_flag(monkeypatch):
    """可控暂停标志：替换 task_manager.is_pause_requested。"""
    state = {"requested": False}

    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested",
                        lambda tid: state["requested"])
    return state


# ═══════════════════════════════════════════════════
# RUNNING 中暂停：当前节点完成，下一节点执行次数 = 0
# ═══════════════════════════════════════════════════

def test_pause_at_b_stops_c(pg, abc3):
    graph, calls = abc3
    record = TaskManager.create(f"pause-user-{uuid.uuid4().hex[:8]}", "暂停测试")

    from backend.orchestration.checkpoint import TaskGraphExecutor, TaskPaused

    executor = TaskGraphExecutor(graph=graph, poll_control_flags=True)

    # 在 B 节点边界后置位暂停标志（模拟 RUNNING 中收到 pause 请求）
    def _paused(tid: str) -> bool:
        return calls["step_b"] >= 1

    executor._paused = _paused  # type: ignore[method-assign]
    executor._cancelled = lambda tid: False  # type: ignore[method-assign]

    with pytest.raises(TaskPaused):
        executor.execute(record)
    # 当前节点 B 已完成，C 从未开始
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 0}

    # Worker 捕获路径落 PAUSED（复刻 impl 语义）
    TaskManager.mark_paused(record.id, message="用户暂停")
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.PAUSED
    # checkpoint 指向最后成功节点：current_node=B，行内 checkpoint_id=B 的结果 checkpoint
    assert row.current_node == "step_b"
    snap = graph.get_state({"configurable": {"thread_id": row.thread_id}})
    assert snap.next == ("step_c",)   # 恢复指针 = C（尚未执行；流放弃时 LangGraph 补写中断点）
    abort_cp = snap.config["configurable"]["checkpoint_id"]
    assert row.checkpoint_id != row.thread_id
    assert row.checkpoint_id < abort_cp          # 行内值是中断点之前的 B 结果 checkpoint


def test_pause_via_impl_full_flow(pg, abc3, monkeypatch):
    """全链路（impl 级）：lease → 执行 → 节点边界发现标志 → TaskPaused → PAUSED 落库。"""
    graph, calls = abc3
    record = TaskManager.create(f"pause-user-{uuid.uuid4().hex[:8]}", "暂停测试")

    from backend.orchestration.checkpoint import task_executor as te

    monkeypatch.setattr(te, "build_task_graph", lambda: graph)
    # 第一个节点（A）边界完成后即置位：验证"当前节点完成后下一节点不开始"
    def _paused(tid: str) -> bool:
        return calls["step_a"] >= 1

    monkeypatch.setattr("backend.tasks.task_manager.is_pause_requested", _paused)
    monkeypatch.setattr("backend.tasks.task_manager.is_cancel_requested",
                        lambda tid: False)

    result = _run_impl(pg, record.id)
    assert result["status"] == "PAUSED"
    assert calls == {"step_a": 1, "step_b": 0, "step_c": 0}   # 下一节点执行次数=0

    row = pg.get_task(record.id)
    assert row.status == TaskStatus.PAUSED
    assert row.current_node == "step_a"                        # checkpoint 指向最后成功节点


# ═══════════════════════════════════════════════════
# pause_task 编排入口：PENDING 队列内暂停 / 幂等 / 终态拒绝
# ═══════════════════════════════════════════════════

def test_pause_pending_task_lands_paused_without_worker(pg):
    from backend.tasks import task_manager

    record = TaskManager.create(f"pause-user-{uuid.uuid4().hex[:8]}", "队列内暂停")
    result = task_manager.pause_task(record.id)
    assert result == {"ok": True, "already": False, "status": "PAUSED",
                      "mode": "queued"}
    row = pg.get_task(record.id)
    assert row.status == TaskStatus.PAUSED
    # Worker 租约不能认领 PAUSED（不会偷偷开跑）
    assert pg.try_acquire_lease(record.id, worker="worker-a") is None


def test_pause_idempotent(pg):
    from backend.tasks import task_manager

    record = TaskManager.create(f"pause-user-{uuid.uuid4().hex[:8]}", "重复暂停")
    first = task_manager.pause_task(record.id)
    second = task_manager.pause_task(record.id)          # 重复 pause 幂等
    assert first["ok"] and second == {"ok": True, "already": True,
                                      "status": "PAUSED"}
    # 状态机层面 PAUSED→PAUSED 自转换同样幂等（进度刷新不炸）
    TaskManager.mark_paused(record.id, message="再次暂停请求")
    assert pg.get_task(record.id).status == TaskStatus.PAUSED


def test_pause_terminal_rejected(pg):
    from backend.tasks import task_manager

    record = TaskManager.create(f"pause-user-{uuid.uuid4().hex[:8]}", "终态拒绝")
    TaskManager.mark_running(record.id)
    TaskManager.mark_success(record.id)
    with pytest.raises(ValueError):
        task_manager.pause_task(record.id)


def test_pause_unknown_task(pg):
    from backend.tasks import task_manager

    with pytest.raises(LookupError):
        task_manager.pause_task(str(uuid.uuid4()))
