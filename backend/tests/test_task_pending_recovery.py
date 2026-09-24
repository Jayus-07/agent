"""tests/test_task_pending_recovery.py — Phase3 STOP B：PENDING Recovery Accelerator。

覆盖 STOP B 任务书 §27 测试矩阵 T1-T12：
  T1  stale orphan（无 broker 消息）→ republish = 1
  T2  fresh pending（age < threshold）→ 不候选、不重投
  T3  deferred pending（not_before 未到）→ 不候选；到期后 → 候选+重投
  T4  terminal（SUCCESS/FAILED/CANCELLED/PAUSED）→ 永不候选
  T5  并发 sweeper 同一 task → recovery claim = 1（DB CAS）
  T6  publish 失败 → 保持 PENDING，cooldown 过后可再次恢复
  T7  recovery 不消耗 RetryPolicy budget
  T8  recovery 经 QueueRouter（main→agent / rag_index→redispatch 链）
  T9  resume_task(PENDING) 仍为 no-op（用户语义与 delivery recovery 分离）
  T10 rag_index 孤儿行（publish 失败残留）可被 sweeper 发现并重投
  T11 duplicate delivery → lease 唯一仲裁
  T12 计数超限 → FAILED(DELIVERY_RECOVERY_EXHAUSTED) 可重试终态

隔离策略：共享开发库存在其他会话的真实 stale PENDING 行——
- SQL 谓词语义：直接对 find_stale_pending 做 membership 断言（行级）
- orchestrator 流程：受控候选注入（monkeypatch find_stale_pending 按序返回），
  dispatch 边界一律 recorder（broker 外部边界，允许 mock）
"""
from __future__ import annotations

import threading

import pytest

from backend.models.task import TaskStatus


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过 pending recovery 测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def recorder(monkeypatch):
    """apply_async / redispatch recorder：broker 外部边界 mock。"""
    calls = {"agent": [], "redispatch": []}

    class _FakeResult:
        id = "test-celery-id"

    class _FakeTask:
        def apply_async(self, args=None, kwargs=None, queue=None, **kw):
            calls["agent"].append((args[0] if args else None, queue))
            return _FakeResult()

    def _fake_redispatch(task_id: str, dispatch_type: str = "initial"):
        calls["redispatch"].append((task_id, dispatch_type))
        return "test-celery-id"

    monkeypatch.setattr("backend.tasks.agent_tasks.execute_agent_task",
                        _FakeTask())
    monkeypatch.setattr("backend.tasks.task_manager._redispatch_index",
                        _fake_redispatch)
    return calls


@pytest.fixture()
def scan(monkeypatch):
    """受控候选注入：orchestrator 两次 find_stale_pending（候选/收口）按序返回。"""
    def _install(*results_in_order):
        # 注意：_candidates 辅助（membership 断言）与 orchestrator 共用同一
        # 模块属性——安装后它的每次调用也消耗一段；耗尽后回落 []（收口批
        # 为空的安全默认），避免 StopIteration 破坏调用方。
        seq = [list(r) for r in results_in_order]
        state = {"i": 0}

        def _fake(**kw):
            i = state["i"]
            state["i"] += 1
            return seq[i] if i < len(seq) else []

        monkeypatch.setattr(
            "backend.services.task_service.find_stale_pending", _fake)
    return _install


@pytest.fixture()
def rows(pg):
    created: list[str] = []

    def _make(**kw) -> str:
        record = pg.create_task(
            kw.get("user_id", "pending-recovery-test"),
            kw.get("query", "test"),
            graph_name=kw.get("graph_name", "main"),
            extra_input=kw.get("extra_input"))
        task_id = str(record.id)
        created.append(task_id)
        return task_id

    yield _make

    with pg._conn() as conn, conn.cursor() as cur:
        for task_id in created:
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def _age(pg, task_id: str, seconds: int,
         *, queued: bool = True, not_before_seconds: int | None = None,
         recovery_count: int | None = None) -> None:
    """把行做成 stale（或设置 not_before / 计数）——测试场景构造。"""
    queued_expr = "queued_at = now() - (%s || ' seconds')::interval" if queued \
        else "queued_at = NULL"
    extra = ""
    args: list = [str(seconds)] if queued else []
    if not_before_seconds is not None:
        extra += ", dispatch_not_before_at = now() + (%s || ' seconds')::interval"
        args.append(str(not_before_seconds))
    if recovery_count is not None:
        extra += ", pending_recovery_count = %s"
        args.append(int(recovery_count))
    args += [str(seconds), str(seconds), task_id]
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE tasks SET {queued_expr}{extra}, "
            "created_at = now() - (%s || ' seconds')::interval, "
            "updated_at = now() - (%s || ' seconds')::interval "
            "WHERE id = %s",
            args)


def _set_status(pg, task_id: str, status: TaskStatus) -> None:
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET status = %s WHERE id = %s",
                    (status.value, task_id))


def _row(pg, task_id: str):
    return pg.get_task(task_id)


def _candidates(pg, task_id: str) -> bool:
    """SQL 谓词行级断言：该行是否为 recovery 候选（默认配置阈值）。"""
    from backend.config.tasks import (TASK_PENDING_RECOVERY_AFTER_SECONDS,
                                      TASK_PENDING_RECOVERY_COOLDOWN_SECONDS,
                                      TASK_PENDING_RECOVERY_MAX_AGE_SECONDS)

    ids = pg.find_stale_pending(
        threshold_seconds=TASK_PENDING_RECOVERY_AFTER_SECONDS,
        cooldown_seconds=TASK_PENDING_RECOVERY_COOLDOWN_SECONDS,
        max_age_seconds=TASK_PENDING_RECOVERY_MAX_AGE_SECONDS, limit=10000)
    return task_id in ids


# ── T1：stale orphan → republish = 1 ─────────────────────────
def test_t1_stale_orphan_republished(pg, rows, recorder, scan):
    task_id = rows()
    _age(pg, task_id, 600, queued=False)  # queued_at NULL = publish 失败孤儿
    assert _candidates(pg, task_id)       # SQL 谓词判为候选
    scan([task_id], [])                   # 候选=本行；收口批=空
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    recover_stale_pending_tasks()
    assert (task_id, "agent") in recorder["agent"]
    row = _row(pg, task_id)
    assert row.status == TaskStatus.PENDING  # republish 不改状态，仍待拾取
    assert row.pending_recovery_count == 1
    assert row.pending_recovery_last_at is not None


# ── T2：fresh pending → 不候选不重投 ─────────────────────────
def test_t2_fresh_pending_not_recovered(pg, rows, recorder, scan):
    task_id = rows()
    _age(pg, task_id, 0)
    assert not _candidates(pg, task_id)
    scan([task_id], [])  # 即使被注入候选，claim CAS 也会拒绝（阈值谓词）
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    recover_stale_pending_tasks()
    assert not any(tid == task_id for tid, _q in recorder["agent"])
    assert _row(pg, task_id).pending_recovery_count == 0


# ── T3：deferred pending（not_before durable 证据）────────────
def test_t3_intentional_defer_not_recovered_until_due(pg, rows, recorder, scan):
    task_id = rows()
    _age(pg, task_id, 600, not_before_seconds=300)  # defer 300s 未到期
    assert not _candidates(pg, task_id)  # not_before durable 排除
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([task_id],  # 消耗段：①到期前 _candidates
         [task_id],  # ②第一轮 recover 候选（claim 被 not_before 谓词拒绝）
         [task_id],  # ③到期后 _candidates
         [task_id])  # ④第二轮 recover 候选（成功认领+重投）；其后收口走回落 []
    recover_stale_pending_tasks()
    assert not any(tid == task_id for tid, _q in recorder["agent"])

    # 到期后同一行获得接手资格（defer 消息丢失场景的保守接手）
    _age(pg, task_id, 600, not_before_seconds=-10)
    assert _candidates(pg, task_id)
    recover_stale_pending_tasks()
    assert (task_id, "agent") in recorder["agent"]


# ── T4：terminal states → 永不候选 ───────────────────────────
@pytest.mark.parametrize("status", [TaskStatus.SUCCESS, TaskStatus.FAILED,
                                    TaskStatus.CANCELLED, TaskStatus.PAUSED])
def test_t4_terminal_never_recovered(pg, rows, recorder, status, scan):
    task_id = rows()
    _age(pg, task_id, 600)
    _set_status(pg, task_id, status)
    assert not _candidates(pg, task_id)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([task_id], [])  # 即使注入，claim CAS 状态谓词拒绝
    recover_stale_pending_tasks()
    assert _row(pg, task_id).status == status  # 状态不被触碰


# ── T5：并发 sweeper → claim = 1 ──────────────────────────────
def test_t5_concurrent_claim_exactly_one_winner(pg, rows):
    task_id = rows()
    _age(pg, task_id, 600)

    winners: list = []
    lock = threading.Lock()

    def _claim():
        claim = pg.claim_stale_pending_for_recovery(
            task_id, threshold_seconds=60, cooldown_seconds=120,
            max_recovery_count=5)
        with lock:
            winners.append(claim)

    threads = [threading.Thread(target=_claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    succeeded = [w for w in winners if w is not None]
    assert len(succeeded) == 1
    assert succeeded[0]["id"] == task_id
    assert succeeded[0]["pending_recovery_count"] == 1
    assert succeeded[0]["workflow"] == "main"


# ── T6：publish 失败 → PENDING + cooldown 后可再恢复 ──────────
def test_t6_publish_failure_keeps_pending_and_retries_after_cooldown(
        pg, rows, scan, monkeypatch):
    task_id = rows()
    _age(pg, task_id, 600)

    def _boom(record, *, dispatch_type="initial"):
        raise ConnectionError("broker down")

    monkeypatch.setattr("backend.tasks.task_manager.dispatch_task", _boom)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([task_id], [])
    result = recover_stale_pending_tasks()
    assert result["publish_failed"] == 1
    row = _row(pg, task_id)
    assert row.status == TaskStatus.PENDING  # 不卡死不落 FAILED
    assert row.pending_recovery_count == 1
    assert row.pending_recovery_last_at is not None

    # cooldown 内：候选注入后 claim CAS 仍拒绝（冷却谓词）
    scan([task_id], [])
    result2 = recover_stale_pending_tasks()
    assert result2["claimed"] == 0
    assert _row(pg, task_id).pending_recovery_count == 1

    # cooldown 过后（last_at 拨旧）再次恢复成功
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET pending_recovery_last_at = "
            "now() - '600 seconds'::interval WHERE id = %s", (task_id,))
    monkeypatch.setattr("backend.tasks.task_manager.dispatch_task",
                        lambda record, *, dispatch_type="initial": "ok")
    scan([task_id], [])
    result3 = recover_stale_pending_tasks()
    assert result3["published"] == 1
    assert _row(pg, task_id).pending_recovery_count == 2


# ── T7：recovery 不消耗 RetryPolicy budget ────────────────────
def test_t7_recovery_does_not_touch_retry_budget(pg, rows, recorder, scan):
    task_id = rows()
    _age(pg, task_id, 600)
    before = _row(pg, task_id)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([task_id], [])
    recover_stale_pending_tasks()
    after = _row(pg, task_id)
    assert after.retry_count == before.retry_count == 0
    assert after.max_retries == before.max_retries  # budget 快照不变
    assert after.pending_recovery_count == 1        # delivery 计数独立
    assert after.recovery_count == 0                # RUNNING 域计数不动


# ── T8：recovery 经 QueueRouter（无硬编码队列）────────────────
def test_t8_recovery_routes_via_queue_router(pg, rows, recorder, scan):
    agent_id = rows()  # main
    index_id = rows(graph_name="rag_index",
                    extra_input={"index_kwargs": {"upload_id": "u1"}})
    _age(pg, agent_id, 600)
    _age(pg, index_id, 600, queued=False)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([agent_id, index_id], [])
    recover_stale_pending_tasks()
    # main → enqueue_task（queue=QueueRouter 解析的 agent 物理队列）
    assert (agent_id, "agent") in recorder["agent"]
    # rag_index → _redispatch_index（workflow dispatcher 表，C6 契约链路）
    assert (index_id, "recovery") in recorder["redispatch"]


# ── T9：resume_task(PENDING) 保持 no-op ──────────────────────
def test_t9_resume_pending_still_noop(pg, rows, recorder):
    task_id = rows()
    _age(pg, task_id, 600)
    from backend.tasks import task_manager

    record = task_manager.resume_task(task_id, "")
    assert record.status == TaskStatus.PENDING
    assert not any(tid == task_id for tid, _q in recorder["agent"])


# ── T10：rag_index 孤儿行可被 sweeper 恢复 ────────────────────
def test_t10_rag_index_orphan_recovered(pg, rows, recorder, scan):
    task_id = rows(graph_name="rag_index",
                   extra_input={"index_kwargs": {"upload_id": "u2",
                                                 "filepath": "/tmp/x.pdf"}})
    _age(pg, task_id, 600, queued=False)  # INSERT 成功 + publish 失败的世界态
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([task_id], [])
    recover_stale_pending_tasks()
    assert (task_id, "recovery") in recorder["redispatch"]


# ── T11：duplicate delivery → lease 唯一仲裁 ──────────────────
def test_t11_duplicate_delivery_single_lease_authority(pg, rows):
    task_id = rows()
    from backend.config.tasks import TASK_LEASE_TTL_SECONDS

    first = pg.try_acquire_lease(
        task_id, worker="w1", lease_ttl_seconds=TASK_LEASE_TTL_SECONDS)
    assert first
    second = pg.try_acquire_lease(
        task_id, worker="w2", lease_ttl_seconds=TASK_LEASE_TTL_SECONDS)
    assert second is None  # 第二条消息（原消息/redelivery）拿不到租约
    assert _row(pg, task_id).execution_id == first


# ── T12：计数超限 → FAILED 终态收口 ───────────────────────────
def test_t12_exhausted_pending_closed_to_failed(pg, rows, scan):
    task_id = rows()
    _age(pg, task_id, 600, recovery_count=5)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    scan([], [task_id])  # 候选批不含超限行；收口批=本行
    result = recover_stale_pending_tasks()
    assert result["exhausted"] == 1
    row = _row(pg, task_id)
    assert row.status == TaskStatus.FAILED
    assert row.error_type == "DELIVERY_RECOVERY_EXHAUSTED"
    # FAILED 是可恢复终态：管理端 retry 通道（resumable）可用
    assert row.status in TaskStatus.resumable()


# ── 开关关闭 → 完全惰性 ───────────────────────────────────────
def test_disabled_sweeper_is_inert(pg, rows, recorder, monkeypatch):
    task_id = rows()
    _age(pg, task_id, 600)
    monkeypatch.setattr("backend.config.tasks.TASK_PENDING_RECOVERY_ENABLED",
                        False)
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    result = recover_stale_pending_tasks()
    assert result["enabled"] is False
    assert result["claimed"] == 0
    assert not any(tid == task_id for tid, _q in recorder["agent"])


# ── defer 写入 not_before 的原子性（release_lease_for_defer 参数）──
def test_release_lease_for_defer_writes_not_before(pg, rows):
    task_id = rows()
    from backend.config.tasks import TASK_LEASE_TTL_SECONDS

    lease = pg.try_acquire_lease(
        task_id, worker="w1", lease_ttl_seconds=TASK_LEASE_TTL_SECONDS)
    assert lease
    ok = pg.release_lease_for_defer(task_id, lease, not_before_seconds=45.0)
    assert ok
    row = _row(pg, task_id)
    assert row.status == TaskStatus.PENDING
    assert row.dispatch_not_before_at is not None
    assert not _candidates(pg, task_id)  # not_before 窗口内不候选
