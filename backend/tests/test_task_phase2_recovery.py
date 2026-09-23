"""tests/test_task_phase2_recovery.py — Phase2 Step1：Worker crash 自动恢复。

覆盖（对齐 Step1 停点验收）：
  A 前置/ B 前置：租约认领/接管语义（PENDING 认领、过期 RUNNING 接管、活跃租约拒绝）
  C 执行期 fencing：旧 execution 的 progress/checkpoint/终态写全部被拒，
    旧 execution_id 无法污染新 execution
  D 重复 recovery：两个 sweeper 并发扫描同一 stale task → 只有一个成功重投，
    recovery_count=1，无双执行链；恢复次数耗尽 → FAILED(ZOMBIE_RECONCILED)；
    重投失败 → 回滚认领且保持 stale 语义
  E PAUSED redelivery：不 acquire、不执行、状态保持 PAUSED
  F CANCELLED / SUCCESS / WAITING_USER redelivery：NO-OP
  心跳：LeaseHeartbeat 周期续租 + 租约被接管后 lost 置位

策略：真实 agent_memory（不可达则 skip 整组）；broker 入队用 recorder 替身
（外部依赖边界，允许 mock）；Redis 控制标志用 fake。
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from backend.models.task import TaskLeaseLost, TaskStatus


@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达则跳过整组用例。"""
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 Phase2 恢复测试: {e}")
    from backend.services import task_service

    return task_service


class _FakeRedis:
    """最小 Redis 桩：控制标志一律不存在，publish 记录。"""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def set(self, key, value, ex=None):
        pass

    def delete(self, *keys):
        pass

    def exists(self, key):
        return 0

    def publish(self, channel, message):
        self.published.append((str(channel), str(message)))


@pytest.fixture()
def fake_redis(monkeypatch):
    from backend.tasks import task_manager

    fake = _FakeRedis()
    monkeypatch.setattr(task_manager, "_redis", lambda: fake)
    return fake


# ── 工具 ─────────────────────────────────────────────
def _expire_lease(pg, task_id: str, seconds: int = 300) -> None:
    """把租约回拨到已过期（免真实等待 TTL）。"""
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET lease_expires_at = "
            "now() - (%s || ' seconds')::interval WHERE id = %s",
            (str(seconds), task_id),
        )


def _set_recovery_count(pg, task_id: str, count: int) -> None:
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET recovery_count = %s WHERE id = %s",
                    (count, task_id))


def _get_status(pg, task_id: str) -> TaskStatus:
    record = pg.get_task(task_id)
    assert record is not None
    return record.status


# ═══════════════════════════════════════════════════
# 租约认领 / 接管语义（Case A/B 前置）
# ═══════════════════════════════════════════════════

def test_lease_acquire_and_active_reject(pg):
    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "租约认领测试")
    try:
        first = pg.try_acquire_lease(task.id, worker="w1")
        assert first
        # 活跃租约：第二个 executor 不得认领（max_concurrent_executor=1）
        assert pg.try_acquire_lease(task.id, worker="w2") is None
        # 心跳续租推进过期窗口
        assert pg.renew_lease(task.id, first)
        assert pg.check_lease_active(task.id, first)
        # 错误 execution_id 的续租/校验全部失败
        assert not pg.renew_lease(task.id, "bogus")
        assert not pg.check_lease_active(task.id, "bogus")
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


def test_lease_takeover_after_expiry(pg):
    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "租约过期接管测试")
    try:
        old = pg.try_acquire_lease(task.id, worker="w1")
        assert old
        _expire_lease(pg, task.id)
        # 过期租约 → 新 Worker 接管成功，换发新 execution_id
        new = pg.try_acquire_lease(task.id, worker="w2")
        assert new and new != old
        assert pg.check_lease_active(task.id, new)
        assert not pg.check_lease_active(task.id, old)
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


# ═══════════════════════════════════════════════════
# Case C：执行期 fencing —— 旧 execution 无法污染新 execution
# ═══════════════════════════════════════════════════

def test_case_c_fencing_blocks_old_execution(pg):
    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "fencing 测试")
    try:
        old = pg.try_acquire_lease(task.id, worker="w1")
        assert old
        _expire_lease(pg, task.id)
        new = pg.try_acquire_lease(task.id, worker="w2")
        assert new

        # 旧 Worker 苏醒：全部执行期写必须被拒
        assert not pg.update_progress(task.id, "planner", "旧worker进度",
                                      execution_id=old)
        assert not pg.append_checkpoint(task_id=task.id, node_name="planner",
                                        state={"evil": True}, execution_id=old)
        with pytest.raises(TaskLeaseLost):
            pg.update_status(task.id, TaskStatus.SUCCESS, output={"evil": True},
                             execution_id=old)
        with pytest.raises(TaskLeaseLost):
            pg.update_status(task.id, TaskStatus.FAILED,
                             error_message="旧worker终审", execution_id=old)

        # 旧 execution 的写没有产生任何痕迹
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.RUNNING
        assert record.execution_id == new
        assert record.current_node != "planner"
        # checkpoint 表无旧 Worker 的幽灵行
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM agent_checkpoints WHERE task_id = %s",
                (task.id,))
            assert int(cur.fetchone()[0]) == 0

        # 新 owner 写入全部正常
        assert pg.update_progress(task.id, "planner", "新owner进度",
                                  execution_id=new)
        assert pg.append_checkpoint(task_id=task.id, node_name="planner",
                                    state={"ok": True}, execution_id=new)
        assert pg.renew_lease(task.id, new)
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent_checkpoints WHERE task_id = %s",
                        (task.id,))
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


# ═══════════════════════════════════════════════════
# Case D：重复 Recovery —— 并发 sweep 只有一个成功；耗尽收口；失败回滚
# ═══════════════════════════════════════════════════

@pytest.fixture()
def sweep_env(monkeypatch):
    """sweep 依赖替身：入队 recorder（broker 是外部依赖）；fake redis 标志。"""
    from backend.tasks import task_manager

    monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())
    dispatched: list[str] = []

    def _fake_enqueue(record, *args, **kwargs):
        # 模拟真实 enqueue_task 契约：入队成功后回填 celery id + queue
        from backend.services import task_service

        dispatched.append(record.id)
        task_service.mark_queued(record.id, "celery-fake-id", queue=record.queue)
        return "celery-fake-id"

    monkeypatch.setattr(task_manager, "enqueue_task", _fake_enqueue)
    return dispatched


def test_case_d_concurrent_sweep_single_recovery(pg, sweep_env):
    dispatched = sweep_env
    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "并发 sweep 幂等测试")
    try:
        lease = pg.try_acquire_lease(task.id, worker="w1")
        assert lease
        _expire_lease(pg, task.id)

        barrier = threading.Barrier(2)
        results: list[dict] = []

        def _run_sweep():
            from backend.tasks.task_manager import sweep_stale_executions

            barrier.wait()
            results.append(sweep_stale_executions())

        threads = [threading.Thread(target=_run_sweep) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 两个 sweeper 并发：该任务只被成功恢复一次（无双消息执行链）
        total_recovered = sum(1 for r in results if task.id in r["recovered"])
        assert total_recovered == 1
        assert dispatched.count(task.id) == 1
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.PENDING
        assert record.recovery_count == 1
        assert record.celery_task_id == "celery-fake-id"
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


def test_case_d_recovery_exhausted_fails_final(pg, sweep_env):
    from backend.config.tasks import TASK_MAX_LEASE_RECOVERIES
    from backend.tasks.task_manager import sweep_stale_executions

    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "恢复耗尽收口测试")
    try:
        lease = pg.try_acquire_lease(task.id, worker="w1")
        assert lease
        _set_recovery_count(pg, task.id, TASK_MAX_LEASE_RECOVERIES)
        _expire_lease(pg, task.id)

        result = sweep_stale_executions()
        assert task.id in result["exhausted"]
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.FAILED
        assert record.error_type == "ZOMBIE_RECONCILED"
        assert not pg.check_lease_active(task.id, lease)
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


def test_case_d_redispatch_failure_reverts_claim(pg, monkeypatch):
    """重投失败 → 回滚认领（PENDING→RUNNING）且保持 stale 语义可再次恢复。"""
    from backend.tasks import task_manager
    from backend.tasks.task_manager import sweep_stale_executions

    monkeypatch.setattr(task_manager, "_redis", lambda: _FakeRedis())

    def _broken_enqueue(record, *args, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(task_manager, "enqueue_task", _broken_enqueue)

    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "重投失败回滚测试")
    try:
        lease = pg.try_acquire_lease(task.id, worker="w1")
        assert lease
        _expire_lease(pg, task.id)

        result = sweep_stale_executions()
        assert task.id not in result["recovered"]
        record = pg.get_task(task.id)
        # 回滚到 RUNNING（保持过期租约语义），下一轮 sweep 可再次恢复
        assert record.status == TaskStatus.RUNNING
        assert record.recovery_count == 1
        stale = pg.find_stale_executions(grace_seconds=0,
                                         legacy_threshold_seconds=0, limit=100)
        assert task.id in stale
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


# ═══════════════════════════════════════════════════
# Case E / F：显式状态短路 —— 消息不得唤醒非执行态任务
# ═══════════════════════════════════════════════════

def _impl_status(pg, task_id: str) -> dict:
    from backend.tasks.agent_tasks import execute_agent_task_impl

    return execute_agent_task_impl(task_id)


def test_case_e_paused_redelivery_noop(pg):
    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "PAUSED redelivery 测试")
    try:
        pg.update_status(task.id, TaskStatus.PAUSED, progress="用户暂停")
        result = _impl_status(pg, task.id)
        assert result["skipped"] is True
        record = pg.get_task(task.id)
        assert record.status == TaskStatus.PAUSED
        # 未 acquire：无租约产生
        assert record.execution_id == ""
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


def test_case_f_terminal_redelivery_noop(pg):
    for status in (TaskStatus.SUCCESS, TaskStatus.CANCELLED,
                   TaskStatus.WAITING_USER):
        user = f"p2-{uuid.uuid4().hex[:8]}"
        task = pg.create_task(user, f"{status.value} redelivery 测试")
        try:
            if status == TaskStatus.WAITING_USER:
                pg.update_status(task.id, TaskStatus.RUNNING, progress="x")
                pg.update_status(task.id, TaskStatus.WAITING_USER)
            else:
                pg.update_status(task.id, TaskStatus.RUNNING, progress="x")
                pg.update_status(task.id, status)
            result = _impl_status(pg, task.id)
            if status == TaskStatus.SUCCESS:
                assert result["status"] == "SUCCESS_NOOP"
            elif status == TaskStatus.CANCELLED:
                assert result["status"] == "CANCELLED"
            else:
                assert result["skipped"] is True
            # 终态/人工态未被消息改变
            assert _get_status(pg, task.id) == status
        finally:
            with pg._conn() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


def test_index_runtime_success_redelivery_noop(pg):
    """rag_index 侧：SUCCESS 行收到重复消息 → NO-OP，不重复索引。"""
    from backend.tasks.index_task_runtime import run_with_task_state

    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "index SUCCESS 短路测试",
                          graph_name="rag_index",
                          extra_input={"index_kwargs": {"upload_id": "u1"}})
    try:
        pg.update_status(task.id, TaskStatus.RUNNING, progress="x")
        pg.update_status(task.id, TaskStatus.SUCCESS)

        def _boom():
            raise AssertionError("SUCCESS 行不得再次执行索引")

        result = run_with_task_state(task.id, "u1", _boom)
        assert result["skipped"] is True
        assert _get_status(pg, task.id) == TaskStatus.SUCCESS
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))


# ═══════════════════════════════════════════════════
# 心跳线程：周期续租 + 租约被接管后 lost 置位
# ═══════════════════════════════════════════════════

def test_heartbeat_renews_and_detects_takeover(pg):
    from backend.tasks.lease_heartbeat import LeaseHeartbeat

    user = f"p2-{uuid.uuid4().hex[:8]}"
    task = pg.create_task(user, "心跳线程测试")
    try:
        lease = pg.try_acquire_lease(task.id, worker="w1",
                                     lease_ttl_seconds=2)
        assert lease
        hb = LeaseHeartbeat(task.id, lease, interval_s=0.05, ttl_s=2)
        hb.start()
        time.sleep(0.3)
        assert not hb.lost
        # 续租推进了租约窗口
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT lease_heartbeat_at, lease_expires_at "
                        "FROM tasks WHERE id = %s", (task.id,))
            row = cur.fetchone()
        assert row[0] is not None and row[1] is not None
        assert row[1] > row[0]

        # 模拟接管：execution_id 被换发 → 下一次续租 rowcount=0 → lost
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE tasks SET execution_id = 'taken-over' "
                        "WHERE id = %s", (task.id,))
        deadline = time.monotonic() + 2.0
        while not hb.lost and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hb.lost
        hb.stop()
    finally:
        with pg._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task.id,))
