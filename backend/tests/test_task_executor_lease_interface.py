"""tests/test_task_executor_lease_interface.py — TaskGraphExecutor 租约接口。

背景（2026-09-23 审查 P0-5）：c837c02 提交了调用方新签名
``execute(record, execution_id=..., heartbeat=...)``，被调方配套改动留在
工作区未提交 → 纯 HEAD 上所有 agent 任务必崩 TypeError。本文件锁定
executor 的明确接口契约（禁止 **kwargs 伪装）：

- legacy 形态 ``execute(record)`` 仍可用（直调/eager 测试）
- 完整形态 ``execute(record, execution_id=..., heartbeat=...)`` 走 fencing
- heartbeat.lost 置位 → 节点边界 TaskLeaseLost（不开始下一节点）
- 成功执行 → SUCCESS + 产出；worker 异常原样上抛（不吞）
- 生命周期契约：executor 只读 heartbeat.lost，不 stop（归调用方 finally）
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest

from backend.models.task import TaskLeaseLost, TaskStatus


def _postgres_saver_available() -> bool:
    try:
        import psycopg  # noqa: F401
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401

        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达则跳过整组（同 Phase2 恢复测试口径）。"""
    pytest.importorskip("psycopg")
    if not _postgres_saver_available():
        pytest.skip("langgraph-checkpoint-postgres 不可用，跳过接口测试")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
        task_service._conn().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过接口测试: {e}")
    from backend.services import task_service

    return task_service


class ABCState(TypedDict, total=False):
    question: str
    step_results: dict
    final_answer: str


def _make_abc_graph():
    """A→B→C 三节点真图（真实 PostgresSaver，同 test_task_checkpoint_recovery）。"""
    from backend.orchestration.checkpoint.task_executor import (
        build_task_checkpointer)
    from langgraph.graph import END, START, StateGraph

    calls = {"step_a": 0, "step_b": 0, "step_c": 0}
    fail = {"step_a": False, "step_b": False, "step_c": False}

    def _mk(name: str):
        def _node(state: dict) -> dict:
            calls[name] += 1
            if fail[name]:
                raise RuntimeError(f"boom: {name} crashed")
            out = {"step_results": {**state.get("step_results", {}),
                                    name: "ok"}}
            if name == "step_c":
                out["final_answer"] = "done"
            return out
        return _node

    wf = StateGraph(ABCState)
    wf.add_node("step_a", _mk("step_a"))
    wf.add_node("step_b", _mk("step_b"))
    wf.add_node("step_c", _mk("step_c"))
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", "step_b")
    wf.add_edge("step_b", "step_c")
    wf.add_edge("step_c", END)
    return wf.compile(checkpointer=build_task_checkpointer()), calls, fail


class _FakeHeartbeat:
    """最小心跳替身：executor 契约只消费 .lost 布尔。"""

    def __init__(self):
        self.lost = False
        self.stopped = False

    def stop(self):
        self.stopped = True


def _executor(graph):
    from backend.orchestration.checkpoint import TaskGraphExecutor

    return TaskGraphExecutor(graph=graph, poll_control_flags=False)


def _cleanup(pg, task_id: str, thread_id: str) -> None:
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
        cur.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s",
                    (thread_id,))
        cur.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s",
                    (thread_id,))
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def test_execute_legacy_signature_still_works(pg):
    """execute(record) 单参形态：直调/eager 测试路径不设防但可用。"""
    user = f"iface-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "接口 legacy 形态测试")
    try:
        graph, calls, _fail = _make_abc_graph()
        out = _executor(graph).execute(pg.get_task(record.id))
        assert out["answer"] == "done"
        assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}
        assert pg.get_task(record.id).status == TaskStatus.SUCCESS
    finally:
        _cleanup(pg, record.id, record.thread_id)


def test_execute_full_signature_with_lease_and_heartbeat(pg):
    """execute(record, execution_id=..., heartbeat=...)：fencing 写全链路通过。"""
    user = f"iface-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "接口完整形态测试")
    try:
        lease = pg.try_acquire_lease(record.id, worker="w-iface")
        assert lease
        hb = _FakeHeartbeat()
        graph, calls, _fail = _make_abc_graph()
        out = _executor(graph).execute(pg.get_task(record.id),
                                      execution_id=lease, heartbeat=hb)
        assert out["answer"] == "done"
        assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}
        final = pg.get_task(record.id)
        assert final.status == TaskStatus.SUCCESS
        assert final.execution_id == lease
        # 生命周期契约：executor 只读 .lost，不越权 stop（归调用方 finally）
        assert hb.stopped is False
    finally:
        _cleanup(pg, record.id, record.thread_id)


def test_execute_lease_lost_via_heartbeat_flag(pg):
    """heartbeat.lost 置位 → 节点边界 TaskLeaseLost，不开始下一节点。"""
    user = f"iface-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "接口租约丢失测试")
    try:
        lease = pg.try_acquire_lease(record.id, worker="w-iface")
        assert lease
        hb = _FakeHeartbeat()
        graph, calls, _fail = _make_abc_graph()
        executor = _executor(graph)

        # step_a 事件交付前置位丢失标志：step_a 自身写点仍走 DB fencing，
        # 随后的 _guard_lease/_publish_fenced 在 step_b 开始前拒绝
        real_stream = graph.stream

        def _stream_with_loss(state, config=None, **kw):
            for evt in real_stream(state, config=config, **kw):
                if evt and next(iter(evt), "") == "step_a":
                    hb.lost = True
                yield evt

        graph.stream = _stream_with_loss

        with pytest.raises(TaskLeaseLost):
            executor.execute(pg.get_task(record.id),
                             execution_id=lease, heartbeat=hb)
        assert calls["step_a"] == 1
        assert calls["step_b"] == 0
    finally:
        _cleanup(pg, record.id, record.thread_id)


def test_execute_worker_exception_propagates(pg):
    """worker 异常原样上抛（不吞、不伪装终态）；终态写由调用方负责。"""
    user = f"iface-{uuid.uuid4().hex[:8]}"
    record = pg.create_task(user, "接口异常透传测试")
    try:
        lease = pg.try_acquire_lease(record.id, worker="w-iface")
        assert lease
        graph, _calls, fail = _make_abc_graph()
        fail["step_b"] = True

        with pytest.raises(RuntimeError, match="boom: step_b"):
            _executor(graph).execute(pg.get_task(record.id),
                             execution_id=lease,
                             heartbeat=_FakeHeartbeat())
        assert pg.get_task(record.id).status == TaskStatus.RUNNING
    finally:
        _cleanup(pg, record.id, record.thread_id)
