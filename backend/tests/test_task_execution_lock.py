"""tests/test_task_execution_lock.py — Phase1 Task Runtime Step3 执行权锁。

停点 3 验收：max_concurrent_executor_per_task = 1
- 租约必须带 owner（worker 名）+ execution_id（租约实例，接管换发可审计）
- 两个 executor 同时认领同一 task：只有 1 个胜出，其余拒绝
- Worker 崩溃（心跳停更）后新 executor 可合法接管（阈值有界，无永久死锁）
- PAUSED 等非 PENDING 态不可认领（必须先 resume 回 PENDING）
"""
from __future__ import annotations

import threading
import uuid

import pytest

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



@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过执行权锁测试: {e}")
    from backend.services import task_service

    return task_service


def _new_task(pg, query: str = "执行权锁测试"):
    from backend.services.task_state import TaskManager

    user = f"lock-user-{uuid.uuid4().hex[:8]}"
    return TaskManager.create(user, query)


def test_lease_carries_owner_and_execution_id(pg):
    record = _new_task(pg)
    lease_id = pg.try_acquire_lease(record.id, worker="worker-a")
    assert lease_id                                    # 认领成功返回租约 id
    row = pg.get_task(record.id)
    assert row.execution_id == lease_id
    assert row.worker == "worker-a"
    assert row.status.value == "RUNNING"
    assert row.started_at is not None


def test_second_executor_rejected(pg):
    """第二个 executor 认领必须失败，且持有者租约不被破坏。"""
    record = _new_task(pg)
    first = pg.try_acquire_lease(record.id, worker="worker-a")
    second = pg.try_acquire_lease(record.id, worker="worker-b")
    assert first and second is None
    row = pg.get_task(record.id)
    assert row.execution_id == first
    assert row.worker == "worker-a"


def test_concurrent_lease_exactly_one_winner(pg):
    """8 个线程并发认领同一任务：胜者恒等于 1（max executor per task = 1）。"""
    record = _new_task(pg)
    n = 8
    barrier = threading.Barrier(n)
    winners: list[str] = []
    lock = threading.Lock()

    def _contender(i: int) -> None:
        barrier.wait()  # 同时起跑，模拟重复触发/重投并发
        lease = pg.try_acquire_lease(record.id, worker=f"worker-{i}")
        if lease:
            with lock:
                winners.append(lease)

    threads = [threading.Thread(target=_contender, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1
    assert pg.get_task(record.id).execution_id == winners[0]


def test_crashed_worker_lease_takeover(pg):
    """Worker 崩溃（心跳停更超阈值）→ 新 executor 合法接管，换发新 execution_id。"""
    record = _new_task(pg)
    old_lease = pg.try_acquire_lease(record.id, worker="worker-dead")
    assert old_lease
    # 回拨租约窗口，模拟硬杀/OOM 后心跳停更的死 Worker
    # （Phase2 Step1：stale 判定唯一权威 = lease_expires_at，NULL 才回落
    #   updated_at——只回拨 updated_at 不会触发接管）
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET updated_at = now() - interval '2 hours', "
            "lease_heartbeat_at = now() - interval '1 hour', "
            "lease_expires_at = now() - interval '1 hour' "
            "WHERE id = %s", (record.id,))
    new_lease = pg.try_acquire_lease(
        record.id, worker="worker-new", stale_running_seconds=1900)
    assert new_lease and new_lease != old_lease        # 接管换发新租约
    row = pg.get_task(record.id)
    assert row.worker == "worker-new"
    assert row.execution_id == new_lease


def test_fresh_running_heartbeat_blocks_takeover(pg):
    """活 Worker 心跳新鲜时不可被接管（阈值口径下不误杀）。"""
    record = _new_task(pg)
    lease = pg.try_acquire_lease(record.id, worker="worker-alive")
    assert lease
    assert pg.try_acquire_lease(
        record.id, worker="worker-evil", stale_running_seconds=1900) is None
    assert pg.get_task(record.id).execution_id == lease


def test_paused_task_not_claimable(pg):
    """PAUSED 不可被 Worker 直接认领——必须先 resume 回 PENDING（Step5 语义）。"""
    from backend.services.task_state import TaskManager

    record = _new_task(pg)
    TaskManager.mark_running(record.id)
    TaskManager.mark_paused(record.id)
    assert pg.try_acquire_lease(record.id, worker="worker-a") is None
    assert pg.get_task(record.id).status.value == "PAUSED"   # 未被抢走
