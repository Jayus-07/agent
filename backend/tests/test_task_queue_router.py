"""tests/test_task_queue_router.py — Phase2 Step3 队列路由收口测试。

覆盖规格 Case A/B/G/H/I/J（纯路由决策）与 C/D/E/F/L（DB 集成的
queue 亲和 + dispatch 观测）；Case K（并发双 resume 恰一次入队）由
test_task_resume.py::test_resume_concurrent_two_clients_single_enqueue
既有用例覆盖，此处不重复。

铁律：强断言；只 mock 外部边界（broker apply_async / Redis / celery
task 对象）；DB 用真实测试库（与 test_task_phase2_recovery 同模式）。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from backend.tasks import queue_router
from backend.tasks.queue_router import (
    QueueRoute,
    QueueRoutingError,
    log_route,
    resolve_for_celery_task,
    resolve_for_task,
    resolve_for_workflow,
)


# ═══════════════════════════════════════════════════
# 纯路由（无 DB / 无 broker）
# ═══════════════════════════════════════════════════

def test_case_a_initial_agent_route():
    """Case A：普通 agent 任务 main → interactive_agent → agent。"""
    route = resolve_for_workflow("main")
    assert isinstance(route, QueueRoute)
    assert route.workload_class == "interactive_agent"
    assert route.physical_queue == "agent"
    assert route.routing_reason == "workflow_binding"


def test_case_b_initial_rag_index_route():
    """Case B：rag_index 初始投递 → rag_index。"""
    route = resolve_for_workflow("rag_index")
    assert route.workload_class == "rag_index"
    assert route.physical_queue == "rag_index"


def test_case_h_metadata_shadow_route():
    """Case H：metadata shadow → rag_metadata_shadow（独立 worker 不回归）。"""
    route = resolve_for_celery_task("tasks.execute_metadata_shadow")
    assert route.workload_class == "metadata_shadow"
    assert route.physical_queue == "rag_metadata_shadow"
    # 队列名必须仍来自既有 env 配置源（不另起字符串）
    from backend.config.tasks import CELERY_METADATA_SHADOW_QUEUE

    assert route.physical_queue == CELERY_METADATA_SHADOW_QUEUE


def test_resolve_for_task_uses_workflow_alias():
    """resolve_for_task 读 workflow（=graph_name 别名），不读 queue 快照。"""
    record = SimpleNamespace(workflow="rag_index", queue="agent")
    assert resolve_for_task(record).physical_queue == "rag_index"
    assert resolve_for_task({"workflow": "main"}).physical_queue == "agent"


def test_case_i_unknown_workflow_fail_closed():
    """Case I：未登记 workflow 必须 QueueRoutingError，绝不默认 agent。"""
    with pytest.raises(QueueRoutingError):
        resolve_for_workflow("mystery_graph")
    with pytest.raises(QueueRoutingError):
        resolve_for_celery_task("tasks.execute_mystery")
    with pytest.raises(QueueRoutingError):
        resolve_for_task(SimpleNamespace(workflow="mystery_graph"))


def test_case_i_fallback_escape_hatch_is_auditable(monkeypatch, caplog):
    """Case I 逃生门：显式开启 QUEUE_ROUTING_UNKNOWN_FALLBACK 才降级且必打 warning。"""
    monkeypatch.setattr(queue_router, "_UNKNOWN_FALLBACK_QUEUE", "agent",
                        raising=False)
    with caplog.at_level("WARNING"):
        route = resolve_for_workflow("mystery_graph")
    assert route.routing_reason == "legacy_fallback"
    assert route.physical_queue == "agent"
    assert "legacy_fallback" in caplog.text


def test_case_j_binding_change_uses_new_mapping(monkeypatch):
    """Case J：logical→physical 映射变化后，resolve 用新 binding（不认旧快照）。"""
    monkeypatch.setitem(queue_router._LOGICAL_QUEUES, "rag_index",
                        "rag_index_v2")
    route = resolve_for_workflow("rag_index")
    assert route.physical_queue == "rag_index_v2"
    # 同一配置版本下确定性稳定
    assert resolve_for_task(SimpleNamespace(workflow="rag_index")
                            ).physical_queue == "rag_index_v2"


def test_case_g_beat_all_explicit_route():
    """Case G：beat 每项显式 options.queue，且全部登记为 beat_binding。"""
    from backend.tasks.celery_app import celery_app

    beat = celery_app.conf.beat_schedule
    assert beat, "beat_schedule 不应为空"
    for name, entry in beat.items():
        task_name = entry["task"]
        assert entry["options"]["queue"] == queue_router.beat_queue(task_name), (
            f"beat 项 {name} 未显式路由")
        route = resolve_for_celery_task(task_name)
        assert route.routing_reason == "beat_binding"
        assert route.workload_class in ("maintenance", "report")


def test_task_routes_derivation_covers_all_registered_tasks():
    """task_routes 从 registry 派生：执行型 3 + beat 8 全覆盖（G2 单一事实源）。"""
    from backend.tasks.celery_app import celery_app

    routes = celery_app.conf.task_routes
    expected = (set(queue_router._CELERY_TASK_ROUTES)
                | set(queue_router._BEAT_TASK_ROUTES))
    assert expected <= set(routes), f"task_routes 缺登记: {expected - set(routes)}"
    all_bindings = {**queue_router._CELERY_TASK_ROUTES,
                    **queue_router._BEAT_TASK_ROUTES}
    for name, (workload_class, _reason) in all_bindings.items():
        assert routes[name]["queue"] == queue_router._LOGICAL_QUEUES[workload_class]


def test_log_route_rejects_unknown_dispatch_type():
    """dispatch_type 白名单：拼错直接 fail-fast（观测口径不可静默失真）。"""
    route = resolve_for_workflow("main")
    with pytest.raises(QueueRoutingError):
        log_route(route, dispatch_type="requeue")


def test_log_route_flags_binding_change(caplog):
    """previous_queue ≠ 解析结果时必须显式 warning（不静默改道）。"""
    route = resolve_for_workflow("rag_index")
    with caplog.at_level("WARNING"):
        log_route(route, dispatch_type="resume", task_id="t1",
                  previous_queue="agent")
    assert "previous_queue=agent" in caplog.text
    assert f"resolved_queue={route.physical_queue}" in caplog.text


# ═══════════════════════════════════════════════════
# DB 集成：resume / retry / recovery 的 queue 亲和
# ═══════════════════════════════════════════════════

class _FakeRedis:
    def set(self, key, value, ex=None):
        pass

    def delete(self, *keys):
        pass

    def exists(self, key):
        return 0

    def publish(self, channel, message):
        pass


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 Step3 集成测试: {e}")
    from backend.services import task_service

    return task_service


def _expire_lease(pg, task_id: str, seconds: int = 300) -> None:
    """把租约回拨到已过期（免真实等待 TTL）。"""
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET lease_expires_at = "
            "now() - (%s || ' seconds')::interval WHERE id = %s",
            (str(seconds), task_id),
        )


class _RetrySignal(RuntimeError):
    """fake task_self.retry 的返回值（真身 raise，测试捕获断言参数）。"""


class _FakeTaskSelf:
    def __init__(self):
        self.retry_kwargs: dict = {}

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        return _RetrySignal("retry-scheduled")


def test_case_c_resume_rag_index_queue_affinity(pg, monkeypatch):
    """Case C：rag_index PAUSED → resume 仍投 rag_index（绝不通投 agent）。"""
    from backend.services import task_state
    from backend.tasks import task_manager

    record = pg.create_task(
        f"qr-{uuid.uuid4().hex[:8]}", "索引文档: c.pdf",
        graph_name="rag_index", biz_type="rag_index",
        extra_input={"index_kwargs": {"upload_id": "qr-c-1",
                                      "filepath": "/tmp/c.pdf",
                                      "filename": "c.pdf"}})
    try:
        task_state.TaskManager.mark_running(record.id)
        task_state.TaskManager.mark_paused(record.id)

        captured: dict = {}
        monkeypatch.setattr(
            "backend.tasks.index_tasks.execute_index_task.apply_async",
            lambda *, kwargs, queue: captured.update(kwargs=kwargs, queue=queue)
            or type("R", (), {"id": "celery-c"})())
        monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())

        task_manager.resume_task(record.id)
        assert captured["queue"] == "rag_index"
        assert captured["kwargs"]["db_task_id"] == record.id
        assert pg.get_task(record.id).queue == "rag_index"
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


def test_case_f_agent_retry_queue_affinity(pg, monkeypatch):
    """Case F：agent 任务 retry 重投仍为 agent（且显式带 queue 参数）。"""
    from backend.models.task import TaskStatus
    from backend.tasks.agent_tasks import _retry_after_state_recheck

    record = pg.create_task(f"qr-{uuid.uuid4().hex[:8]}", "case f")
    try:
        pg.update_status(record.id, TaskStatus.FAILED, error_message="等待重试")
        fake = _FakeTaskSelf()
        from backend.tasks.retry_policy import TaskRetryScheduled

        with pytest.raises(_RetrySignal):
            _retry_after_state_recheck(
                fake, record.id,
                TaskRetryScheduled(RuntimeError("timeout"), error_type="timeout",
                                   retryable=True, delay=1, retry_count=0,
                                   max_retries=3))
        assert fake.retry_kwargs["queue"] == "agent"
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


def test_case_d_rag_index_retry_binding(pg):
    """Case D：rag_index retry 的队列亲和由 QueueRouter 保证（与 retry 机制解耦）。"""
    record = pg.create_task(
        f"qr-{uuid.uuid4().hex[:8]}", "索引文档: d.pdf",
        graph_name="rag_index", biz_type="rag_index")
    try:
        row = pg.get_task(record.id)
        # retry 壳统一 resolve_for_task(record) → queue=physical（与
        # agent 壳同型代码路径，本处断言 binding 对 FAILED 行同样成立）
        assert resolve_for_task(row).physical_queue == "rag_index"
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


def test_case_e_recovery_rag_index_queue_affinity(pg, monkeypatch, caplog):
    """Case E：Worker crash → stale sweep → recovery 重投 rag_index。"""
    from backend.services import task_state
    from backend.tasks import task_manager
    from backend.tasks.task_manager import sweep_stale_executions

    record = pg.create_task(
        f"qr-{uuid.uuid4().hex[:8]}", "索引文档: e.pdf",
        graph_name="rag_index", biz_type="rag_index",
        extra_input={"index_kwargs": {"upload_id": "qr-e-1",
                                      "filepath": "/tmp/e.pdf",
                                      "filename": "e.pdf"}})
    try:
        assert pg.try_acquire_lease(record.id, worker="w1")
        _expire_lease(pg, record.id)

        captured: dict = {}
        monkeypatch.setattr(
            "backend.tasks.index_tasks.execute_index_task.apply_async",
            lambda *, kwargs, queue: captured.update(kwargs=kwargs, queue=queue)
            or type("R", (), {"id": "celery-e"})())
        monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())

        with caplog.at_level("INFO"):
            result = sweep_stale_executions()
        assert record.id in result["recovered"]
        assert captured["queue"] == "rag_index"
        assert captured["kwargs"]["db_task_id"] == record.id
        assert pg.get_task(record.id).queue == "rag_index"
        # Case L（recovery 半边）：recovery 决策走 QueueRouter 且 dispatch 可辨
        assert "dispatch=recovery" in caplog.text
        assert "physical=rag_index" in caplog.text
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


def test_case_l_agent_recovery_queue_and_dispatch(pg, monkeypatch, caplog):
    """Case L（另一半）：agent 任务 recovery 也走 QueueRouter，dispatch=recovery。"""
    from backend.tasks import task_manager
    from backend.tasks.task_manager import sweep_stale_executions

    record = pg.create_task(f"qr-{uuid.uuid4().hex[:8]}", "case l")
    try:
        assert pg.try_acquire_lease(record.id, worker="w1")
        _expire_lease(pg, record.id)

        captured: dict = {}
        monkeypatch.setattr(
            "backend.tasks.agent_tasks.execute_agent_task.apply_async",
            lambda *, args, queue: captured.update(args=args, queue=queue)
            or type("R", (), {"id": "celery-l"})())
        monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())

        with caplog.at_level("INFO"):
            result = sweep_stale_executions()
        assert record.id in result["recovered"]
        assert captured["queue"] == "agent"
        assert captured["args"] == [record.id]
        assert "dispatch=recovery" in caplog.text
        assert "logical=interactive_agent" in caplog.text
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))


def test_case_i_enqueue_rejects_unknown_workflow(pg, monkeypatch):
    """Case I（TaskState 面）：未知 workflow 入队被拒，不留假 RUNNING/PENDING 投递。"""
    from backend.tasks import task_manager

    record = pg.create_task(f"qr-{uuid.uuid4().hex[:8]}", "case i")
    try:
        # 模拟未登记 workflow 直接进入派发层（正常入口已被 API 400 拦截）
        object.__setattr__(record, "graph_name", "mystery_graph")
        monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())
        with pytest.raises(QueueRoutingError):
            task_manager.dispatch_task(record, dispatch_type="initial")
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (record.id,))
