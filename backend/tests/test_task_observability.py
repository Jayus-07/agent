"""tests/test_task_observability.py — Phase2-F Worker Observability。

O 矩阵（对齐 §三十九）：
  O1 normal：executor 全链 → trace 绑定/收口 + tasks.trace_id 回填 + 出口指标
  O2 deny：授权 fail-closed → authorization_denied 指标 + trace error 收口
  O3 lease lost：heartbeat.lost → fenced 写指标 + LEASE_LOST 出口 + trace 收口
  O4 recovery：sweep recovered → task_recovery_total
  O5 defer：admission 拒绝早退 → 无 ContextVar/心跳残留（§三十三泄漏回归）
  O6 retry：impl retry 分支 → task_retry_total
  O7 context leak：连续 A/B 任务 → trace/execution ContextVar 不串
  O8 trace export failure：finish 抛异常 → 任务仍成功（best-effort）
  暴露：multiprocess 目录聚合文本含运行时指标

指标断言用 prometheus_client REGISTRY.get_sample_value 真实读值（非 mock
计数器）；trace 断言通过 subscribe/finish 捕获真实 record。
"""
from __future__ import annotations

import os
import uuid

import pytest

from backend.models.task import TaskLeaseLost, TaskStatus


@pytest.fixture(autouse=True)
def _task_auth_enabled(monkeypatch):
    """执行时授权解析放行为合法 editor（边界 mock，同 Phase2 契约测试）。"""
    import backend.security.task_authorization as _ta
    from backend.security.authorization import build_tool_authorization_context

    ctx = build_tool_authorization_context(
        user_id="900001", department="ecom", tenant_id="default",
        roles=("editor",))
    monkeypatch.setattr(_ta, "resolve_task_authorization",
                        lambda uid, tid: ctx)


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 observability 测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def abc3():
    """A→B→C 三节点真图（MemorySaver，同 lease interface 测试口径）。"""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class ABCState(TypedDict, total=False):
        question: str
        step_results: dict
        final_answer: str

    calls = {"step_a": 0, "step_b": 0, "step_c": 0}

    def _mk(name: str):
        def _node(state: dict) -> dict:
            calls[name] += 1
            out = {"step_results": {**state.get("step_results", {}),
                                    name: "ok"}}
            if name == "step_c":
                out["final_answer"] = "done"
            return out
        return _node

    # 节点经 TraceMiddleware 包装（生产 build_graph 同款接线）——
    # 正是本阶段要回归的通路：trace 绑定后节点 span 必须挂上
    from backend.observability.trace_middleware import TraceMiddleware

    mw = TraceMiddleware()
    wf = StateGraph(ABCState)
    wf.add_node("step_a", mw.wrap_sync_node("step_a", _mk("step_a")))
    wf.add_node("step_b", mw.wrap_sync_node("step_b", _mk("step_b")))
    wf.add_node("step_c", mw.wrap_sync_node("step_c", _mk("step_c")))
    wf.add_edge(START, "step_a")
    wf.add_edge("step_a", "step_b")
    wf.add_edge("step_b", "step_c")
    wf.add_edge("step_c", END)
    return wf.compile(), calls


class _FakeHeartbeat:
    def __init__(self):
        self.lost = False


def _executor(graph):
    from backend.orchestration.checkpoint import TaskGraphExecutor

    return TaskGraphExecutor(graph=graph, poll_control_flags=False)


def _metric(name: str, labels: dict | None = None) -> float | None:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels or {})


def _cleanup(pg, task_id: str, thread_id: str) -> None:
    with pg._conn() as conn, conn.cursor() as cur:
        # LangGraph checkpoint 三表按 thread_id；业务 checkpoint/tasks 按 id
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            cur.execute(f"DELETE FROM {table} WHERE thread_id = %s",
                        (thread_id,))
        cur.execute("DELETE FROM agent_checkpoints WHERE task_id = %s",
                    (task_id,))
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def _new_task(pg, query="观测测试"):
    record = pg.create_task(f"9005{uuid.uuid4().int % (10 ** 12):012d}", query)
    return record


# ═══════════════════════════════════════════════════
# O1 — Normal Task
# ═══════════════════════════════════════════════════

def test_o1_normal_task_trace_and_metrics(pg, abc3, monkeypatch):
    from backend.observability.tracer import trace_collector

    graph, calls = abc3
    record = _new_task(pg)
    before = {
        "lease": _metric("task_lease_events_total",
                         {"event": "acquire"}) or 0.0,
    }
    lease = pg.try_acquire_lease(record.id, worker="w-obs")
    assert lease

    captured: dict = {}
    real_finish = trace_collector.finish

    def _capturing_finish(rec, *a, **kw):
        captured["record"] = rec
        return real_finish(rec, *a, **kw)

    monkeypatch.setattr(trace_collector, "finish", _capturing_finish)

    out = _executor(graph).execute(pg.get_task(record.id),
                                   execution_id=lease,
                                   heartbeat=_FakeHeartbeat())
    assert out["answer"] == "done"
    assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}

    # tasks.trace_id 回填 + trace 收口 + 关联标签
    assert pg.get_task(record.id).trace_id, "tasks.trace_id 未回填"
    rec = captured["record"]
    assert rec.status == "success"
    assert rec.session_id == (record.thread_id or f"task-{record.id}")
    assert rec.tags["task_id"] == record.id
    assert rec.tags["execution_id"] == lease
    # TraceMiddleware 节点 span 已挂上（trace is None 直通被打破）
    span_ids = {s.span_id for s in rec.spans}
    assert {"step_a", "step_b", "step_c"} <= span_ids
    # ContextVar 已清（无 dangling）
    assert trace_collector.current() is None
    # 指标：租约认领（service 单点）+ fenced 写
    assert (_metric("task_lease_events_total", {"event": "acquire"})
            == before["lease"] + 1)
    assert (_metric("task_fenced_write_total",
                    {"operation": "progress", "result": "accepted"}) or 0) >= 1
    assert (_metric("task_fenced_write_total",
                    {"operation": "checkpoint", "result": "accepted"}) or 0) >= 1

    _cleanup(pg, record.id, record.thread_id)


# ═══════════════════════════════════════════════════
# O2 — Authorization Deny
# ═══════════════════════════════════════════════════

def test_o2_authorization_deny_metrics_and_trace(pg, abc3, monkeypatch):
    import backend.security.task_authorization as _ta
    from backend.observability.tracer import trace_collector
    from backend.security.task_authorization import TaskAuthorizationDenied

    graph, _calls = abc3
    record = _new_task(pg)
    lease = pg.try_acquire_lease(record.id, worker="w-obs")
    assert lease

    monkeypatch.setattr(_ta, "resolve_task_authorization",
                        lambda uid, tid: (_ for _ in ()).throw(
                            TaskAuthorizationDenied("denied for test")))
    captured: dict = {}
    real_finish = trace_collector.finish

    def _capturing_finish(rec, *a, **kw):
        captured["record"] = rec
        return real_finish(rec, *a, **kw)

    monkeypatch.setattr(trace_collector, "finish", _capturing_finish)

    before = _metric("task_authorization_denied_total",
                     {"workflow": "main"}) or 0.0
    out = _executor(graph).execute(pg.get_task(record.id),
                                   execution_id=lease,
                                   heartbeat=_FakeHeartbeat())
    assert out.get("blocked") is True
    assert pg.get_task(record.id).status == TaskStatus.FAILED
    assert (_metric("task_authorization_denied_total", {"workflow": "main"})
            == before + 1)
    rec = captured["record"]
    assert rec.status == "error"                     # 顶层状态不误标 success
    root = next(s for s in rec.spans if s.parent_id is None)
    assert root.status == "error"                    # root span error 收口
    assert trace_collector.current() is None         # 无 dangling

    _cleanup(pg, record.id, record.thread_id)


# ═══════════════════════════════════════════════════
# O3 — Lease Lost
# ═══════════════════════════════════════════════════

def test_o3_lease_lost_metrics_and_trace(pg, abc3, monkeypatch):
    from backend.observability.tracer import trace_collector

    graph, calls = abc3
    record = _new_task(pg)
    lease = pg.try_acquire_lease(record.id, worker="w-obs")
    assert lease
    hb = _FakeHeartbeat()
    executor = _executor(graph)

    captured: dict = {}
    real_finish = trace_collector.finish

    def _capturing_finish(rec, *a, **kw):
        captured["record"] = rec
        return real_finish(rec, *a, **kw)

    monkeypatch.setattr(trace_collector, "finish", _capturing_finish)

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
    assert calls["step_b"] == 0                       # 不开始下一节点
    rec = captured["record"]
    assert rec.status == "error"
    assert all(s.end_time for s in rec.spans)         # 无 open span 残留
    assert trace_collector.current() is None
    # 注：本场景拒绝源于心跳 lost 标志（DB 租约仍有效），fenced 计数
    # 不产生——DB 级 fenced 拒绝由 case_c/case_g 覆盖，此处不重复断言

    _cleanup(pg, record.id, record.thread_id)


# ═══════════════════════════════════════════════════
# O4 — Recovery metrics
# ═══════════════════════════════════════════════════

def test_o4_recovery_metric(pg, monkeypatch):
    from backend.tasks import task_manager

    record = _new_task(pg, "recovery 观测")
    try:
        assert pg.try_acquire_lease(record.id, worker="w-obs")
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE tasks SET lease_expires_at = "
                        "now() - interval '1 hour' WHERE id = %s",
                        (record.id,))
        dispatched: list[str] = []

        def _fake_enqueue(rec, *a, **kw):
            dispatched.append(rec.id)
            pg.mark_queued(rec.id, "celery-obs", queue=rec.queue)
            return "celery-obs"

        monkeypatch.setattr(task_manager, "enqueue_task", _fake_enqueue)
        monkeypatch.setattr(task_manager, "_redis", lambda: type(
            "R", (), {"set": lambda *a, **k: None,
                      "delete": lambda *a, **k: None,
                      "exists": lambda *a, **k: 0,
                      "publish": lambda *a, **k: None})())

        before = _metric("task_recovery_total", {"result": "recovered"}) or 0.0
        result = task_manager.sweep_stale_executions()
        assert record.id in result["recovered"]
        # 增量断言：sweep 扫全表，同库可能存在其他测试遗留的 stale 行，
        # 只要求本任务被 recovered 且计数单调增（>=1）
        assert (_metric("task_recovery_total", {"result": "recovered"})
                or 0.0) >= before + 1
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


# ═══════════════════════════════════════════════════
# O5 — Admission defer 无 ContextVar/心跳残留
# ═══════════════════════════════════════════════════

def test_o5_defer_no_context_leak(pg, monkeypatch):
    from backend.tasks import agent_tasks
    from backend.tasks.execution_context import get_execution

    record = _new_task(pg, "defer 泄漏回归")
    try:
        # 不预占租约：impl 自己走 PENDING 认领（预占会触发 RUNNING_ELSEWHERE 短路）

        class _Denied:
            allowed = False
            reason = "test_limit"

        monkeypatch.setattr(
            "backend.tasks.admission.acquire_for_execution",
            lambda *a, **kw: _Denied())
        monkeypatch.setattr(
            "backend.tasks.admission.defer_budget_exhausted",
            lambda tid: False)
        monkeypatch.setattr(
            "backend.tasks.admission.note_deferred",
            lambda tid, workflow: 3.0)
        monkeypatch.setattr(
            "backend.tasks.queue_router.resolve_for_task",
            lambda rec: type("R", (), {"physical_queue": "agent",
                                       "workflow": rec.workflow})())
        monkeypatch.setattr(
            "backend.tasks.agent_tasks.execute_agent_task.apply_async",
            lambda **kw: type("R", (), {"id": "x"})())

        out = agent_tasks.execute_agent_task_impl(record.id)
        assert out["status"] == "ADMISSION_DEFERRED"
        # §三十三 泄漏回归：defer 早退路径不得残留执行上下文
        assert get_execution() is None
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


# ═══════════════════════════════════════════════════
# O6 — Retry metric（impl 层，FakeExecutor 隔离图执行）
# ═══════════════════════════════════════════════════

def test_o6_retry_metric(pg, monkeypatch):
    import backend.orchestration.checkpoint as _cp
    from backend.tasks import agent_tasks

    class _FakeExecutor:
        def __init__(self, *a, **kw):
            pass

        def execute(self, record, *, execution_id="", heartbeat=None):
            return {"answer": "ok", "step_results": {}}

    monkeypatch.setattr(_cp, "TaskGraphExecutor", _FakeExecutor)

    record = _new_task(pg, "retry 观测")
    try:
        # 不预占租约：impl 自己认领（预占会触发 RUNNING_ELSEWHERE 短路）
        before = _metric("task_retry_total", {"workflow": "main"}) or 0.0
        before_terminal = _metric("task_terminal_total",
                                  {"workflow": "main",
                                   "status": "SUCCESS"}) or 0.0
        before_dur = _metric("task_execution_duration_seconds_count",
                             {"workflow": "main"}) or 0.0
        out = agent_tasks.execute_agent_task_impl(record.id, retries=1,
                                                  hostname="w-obs")
        assert out["status"] == "SUCCESS"
        assert (_metric("task_retry_total", {"workflow": "main"})
                == before + 1)
        # 出口观测（impl 层）：出口状态 + 执行段耗时
        assert (_metric("task_terminal_total",
                        {"workflow": "main", "status": "SUCCESS"})
                == before_terminal + 1)
        assert (_metric("task_execution_duration_seconds_count",
                        {"workflow": "main"}) == before_dur + 1)
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


# ═══════════════════════════════════════════════════
# O7 — Context Leak：连续 A/B 任务不串
# ═══════════════════════════════════════════════════

def test_o7_context_no_leak_between_tasks(pg, abc3):
    from backend.observability.tracer import trace_collector
    from backend.tasks.execution_context import get_execution

    graph, _calls = abc3
    results = []
    for i in range(2):
        record = _new_task(pg, f"context 隔离 {i}")
        try:
            lease = pg.try_acquire_lease(record.id, worker="w-obs")
            assert lease
            out = _executor(graph).execute(pg.get_task(record.id),
                                           execution_id=lease,
                                           heartbeat=_FakeHeartbeat())
            assert out["answer"] == "done"
            row = pg.get_task(record.id)
            results.append({
                "task_id": record.id,
                "trace_id": row.trace_id,
                "current_after": trace_collector.current(),
                "exec_after": get_execution(),
            })
        finally:
            with pg._conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))

    a, b = results
    assert a["trace_id"] and b["trace_id"] and a["trace_id"] != b["trace_id"]
    # 最终必须 false：A 的上下文在 B 中不可见（ContextVar 已清）
    assert a["current_after"] is None and a["exec_after"] is None
    assert b["current_after"] is None and b["exec_after"] is None
    # B 的 trace 不含 A 的 task_id 痕迹
    assert a["task_id"] not in b["trace_id"]


# ═══════════════════════════════════════════════════
# O8 — Trace Export Failure：观测失败不影响任务
# ═══════════════════════════════════════════════════

def test_o8_trace_failure_best_effort(pg, abc3, monkeypatch):
    from backend.observability.tracer import trace_collector

    graph, calls = abc3
    record = _new_task(pg, "trace 故障 best-effort")
    try:
        lease = pg.try_acquire_lease(record.id, worker="w-obs")
        assert lease

        def _broken_finish(rec, *a, **kw):
            raise RuntimeError("trace backend down")

        monkeypatch.setattr(trace_collector, "finish", _broken_finish)
        out = _executor(graph).execute(pg.get_task(record.id),
                                       execution_id=lease,
                                       heartbeat=_FakeHeartbeat())
        assert out["answer"] == "done"                # 任务不受观测故障影响
        assert calls == {"step_a": 1, "step_b": 1, "step_c": 1}
        assert pg.get_task(record.id).status == TaskStatus.SUCCESS
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


# ═══════════════════════════════════════════════════
# Worker metrics exposure：multiprocess 聚合
# ═══════════════════════════════════════════════════

def test_worker_metrics_multiprocess_render(tmp_path, monkeypatch):
    """multiprocess 目录聚合文本包含运行时指标（暴露链路的聚合半边）。

    prometheus_client 的 ValueClass 在进程首次导入时按 env 定型——测试
    进程早已导入，须由全新子进程（env 指向同一 tmp 目录）产生 multiproc
    文件，本进程只验证 MultiProcessCollector 聚合读取。
    """
    import subprocess
    import sys

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    subprocess.run(
        [sys.executable, "-c",
         "from prometheus_client import Counter\n"
         "c = Counter('probe_metric_total', 'probe')\n"
         "c.inc(3)\n"],
        env={**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)},
        check=True, timeout=60)

    from backend.observability.worker_metrics import render_worker_metrics

    body = render_worker_metrics().decode("utf-8")
    assert "probe_metric_total" in body
    # 运行时指标集已注册（worker 进程同样经此链路聚合暴露）
    from backend.observability import metrics as m

    assert m.task_enqueued_total is not None
    assert m.task_terminal_total is not None
    assert m.task_fenced_write_total is not None
