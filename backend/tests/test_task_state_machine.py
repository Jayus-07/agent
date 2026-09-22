"""tests/test_task_state_machine.py — Phase1 Task Runtime Step1 状态机测试。

验收口径（停点 1）：
- 允许：PENDING→RUNNING、RUNNING→PAUSED、PAUSED→RUNNING、RUNNING→SUCCESS、
  RUNNING→FAILED、RUNNING/PAUSED→CANCELLED
- 禁止：SUCCESS→RUNNING、FAILED→RUNNING、CANCELLED→RUNNING（终态不复活）
- 非法跳转必须明确拒绝（IllegalTaskTransition），且 DB 状态不被破坏
- DB 状态可查询；Celery 状态不是业务状态事实源（全链路零 AsyncResult）

策略：
- 转换表为纯单元测试（无外部依赖）
- 落库流转走 TaskManager + 真实 agent_memory（不可达则 skip 整组）
"""
from __future__ import annotations

import uuid

import pytest

from backend.models.task import IllegalTaskTransition, TaskRecord, TaskStatus
from backend.services.task_state import TaskManager


# ═══════════════════════════════════════════════════
# 纯单元：转换表白名单
# ═══════════════════════════════════════════════════

def test_spec_allowed_transitions():
    allowed = [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.PAUSED),
        (TaskStatus.PAUSED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.SUCCESS),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
        (TaskStatus.PAUSED, TaskStatus.CANCELLED),
    ]
    for cur, tgt in allowed:
        assert cur.can_transition_to(tgt), f"{cur.value} → {tgt.value} 必须允许"


def test_spec_forbidden_terminal_revive():
    """硬禁止口径：终态不得直接回 RUNNING/PAUSED/PENDING（FAILED 回 PENDING 仅限显式重试，见下）。"""
    for terminal in (TaskStatus.SUCCESS, TaskStatus.CANCELLED):
        assert not terminal.can_transition_to(TaskStatus.RUNNING)
        assert not terminal.can_transition_to(TaskStatus.PAUSED)
        assert not terminal.can_transition_to(TaskStatus.PENDING)
    assert not TaskStatus.FAILED.can_transition_to(TaskStatus.RUNNING)
    assert not TaskStatus.FAILED.can_transition_to(TaskStatus.PAUSED)


def test_terminal_fully_closed():
    """SUCCESS/CANCELLED 全封闭；FAILED 仅自转换 + 显式回 PENDING。"""
    all_states = set(TaskStatus)
    for tgt in all_states - {TaskStatus.SUCCESS}:
        assert not TaskStatus.SUCCESS.can_transition_to(tgt)
    for tgt in all_states - {TaskStatus.CANCELLED}:
        assert not TaskStatus.CANCELLED.can_transition_to(tgt)
    for tgt in all_states - {TaskStatus.FAILED, TaskStatus.PENDING}:
        assert not TaskStatus.FAILED.can_transition_to(tgt), \
            f"FAILED → {tgt.value} 必须拒绝（重试只允许显式回 PENDING）"
    assert TaskStatus.FAILED.can_transition_to(TaskStatus.PENDING)
    assert TaskStatus.FAILED.can_transition_to(TaskStatus.FAILED)


def test_running_cannot_requeue_silently():
    """RUNNING 不得悄悄回 PENDING（必须经 PAUSED 或终态）。"""
    assert not TaskStatus.RUNNING.can_transition_to(TaskStatus.PENDING)


def test_resume_requeue_markers_legal():
    """resume 的回队标记：PAUSED/WAITING_USER→PENDING 合法（Worker 租约再进 RUNNING）。"""
    assert TaskStatus.PAUSED.can_transition_to(TaskStatus.PENDING)
    assert TaskStatus.WAITING_USER.can_transition_to(TaskStatus.PENDING)
    assert TaskStatus.WAITING_USER.can_transition_to(TaskStatus.CANCELLED)


def test_queued_task_terminal_paths():
    """队列内（未开跑）取消/入队失败落终态。"""
    assert TaskStatus.PENDING.can_transition_to(TaskStatus.CANCELLED)
    assert TaskStatus.PENDING.can_transition_to(TaskStatus.FAILED)


def test_illegal_transition_exception_carries_context():
    exc = IllegalTaskTransition("t-1", TaskStatus.SUCCESS, TaskStatus.RUNNING)
    assert exc.current == TaskStatus.SUCCESS
    assert exc.target == TaskStatus.RUNNING
    assert "SUCCESS" in str(exc) and "RUNNING" in str(exc)


# ═══════════════════════════════════════════════════
# DB 集成：TaskManager 全生命周期流转（真实 agent_memory）
# ═══════════════════════════════════════════════════

@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达则跳过整组用例。"""
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过状态机测试: {e}")
    from backend.services import task_service

    return task_service


@pytest.fixture()
def record(pg):
    """每用例一条独立任务（uuid 用户，互不污染）。"""
    user = f"sm-user-{uuid.uuid4().hex[:8]}"
    rec = TaskManager.create(
        user, "状态机测试任务", conversation_id=f"conv-{uuid.uuid4().hex[:8]}")
    yield rec
    # 不删行：uuid 用户命名空间隔离（同 test_task_orchestration 约定）


def _refresh(pg, record: TaskRecord) -> TaskRecord:
    return pg.get_task(record.id)


def test_create_persists_taskstate_fields(pg, record):
    assert record.status == TaskStatus.PENDING
    assert record.thread_id == f"task-{record.id}"
    assert record.conversation_id.startswith("conv-")
    assert record.workflow == "main"          # workflow 口径 = graph_name
    row = _refresh(pg, record)
    assert row.conversation_id == record.conversation_id


def test_full_pause_resume_success_flow(pg, record):
    TaskManager.mark_running(record.id, progress="开始")
    mid = _refresh(pg, record)
    assert mid.status == TaskStatus.RUNNING
    assert mid.started_at is not None

    TaskManager.mark_paused(record.id, message="节点 B 后暂停")
    paused = _refresh(pg, record)
    assert paused.status == TaskStatus.PAUSED

    TaskManager.mark_running(record.id, progress="恢复")
    assert _refresh(pg, record).status == TaskStatus.RUNNING

    TaskManager.mark_success(record.id, output={"answer": "ok"})
    done = _refresh(pg, record)
    assert done.status == TaskStatus.SUCCESS
    assert done.output == {"answer": "ok"}
    assert done.finished_at is not None
    assert done.duration_ms is not None       # started_at → finished_at 自动补算


def test_mark_failed_writes_error_code(pg, record):
    TaskManager.mark_running(record.id)
    TaskManager.mark_failed(record.id, error_message="boom",
                            error_code="RuntimeError")
    failed = _refresh(pg, record)
    assert failed.status == TaskStatus.FAILED
    assert failed.error_type == "RuntimeError"   # error_code 口径落 error_type 列
    assert failed.error_message == "boom"
    assert failed.finished_at is not None


def test_mark_cancelled_from_running_and_paused(pg):
    user = f"sm-user-{uuid.uuid4().hex[:8]}"
    r1 = TaskManager.create(user, "运行中取消")
    TaskManager.mark_running(r1.id)
    TaskManager.mark_cancelled(r1.id)
    assert _refresh(pg, r1).status == TaskStatus.CANCELLED

    r2 = TaskManager.create(user, "暂停后取消")
    TaskManager.mark_running(r2.id)
    TaskManager.mark_paused(r2.id)
    TaskManager.mark_cancelled(r2.id)
    assert _refresh(pg, r2).status == TaskStatus.CANCELLED


def test_illegal_jump_rejected_and_db_unchanged(pg, record):
    """规格硬禁止：SUCCESS → RUNNING 必须被明确拒绝，DB 状态不被破坏。"""
    TaskManager.mark_running(record.id)
    TaskManager.mark_success(record.id)
    with pytest.raises(IllegalTaskTransition) as ei:
        TaskManager.mark_running(record.id)
    assert ei.value.current == TaskStatus.SUCCESS
    assert ei.value.target == TaskStatus.RUNNING
    assert _refresh(pg, record).status == TaskStatus.SUCCESS


def test_failed_cannot_jump_to_running(pg, record):
    """规格硬禁止：FAILED → RUNNING；重试必须显式回 PENDING。"""
    TaskManager.mark_running(record.id)
    TaskManager.mark_failed(record.id, error_message="boom")
    with pytest.raises(IllegalTaskTransition):
        TaskManager.mark_running(record.id)
    assert _refresh(pg, record).status == TaskStatus.FAILED
    # 显式重试路径：FAILED → PENDING → RUNNING
    pg.update_status(record.id, TaskStatus.PENDING, progress="重试回队")
    TaskManager.mark_running(record.id)
    assert _refresh(pg, record).status == TaskStatus.RUNNING


def test_cancelled_is_final(pg, record):
    TaskManager.mark_cancelled(record.id)
    with pytest.raises(IllegalTaskTransition):
        TaskManager.mark_running(record.id)
    with pytest.raises(IllegalTaskTransition):
        TaskManager.mark_paused(record.id)
    with pytest.raises(IllegalTaskTransition):
        TaskManager.mark_failed(record.id, error_message="late")
    assert _refresh(pg, record).status == TaskStatus.CANCELLED


def test_concurrent_status_change_revalidated(pg, record):
    """校验与写入之间的并发窗口：他人抢先改状态后按最新态重判并拒绝。"""
    TaskManager.mark_running(record.id)
    # 模拟并发写者：绕过 update_status 直接把状态改成 PAUSED
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET status = 'PAUSED' WHERE id = %s",
                    (record.id,))
    with pytest.raises(IllegalTaskTransition):
        # RUNNING→SUCCESS 本合法，但 DB 已被并发改为 PAUSED → 不允许直跳 SUCCESS
        pg.update_status(record.id, TaskStatus.SUCCESS, progress="late")
    assert _refresh(pg, record).status == TaskStatus.PAUSED


def test_lease_no_longer_claims_failed(pg, record):
    """租约只认领 PENDING；FAILED 必须先显式回 PENDING（重试可审计）。"""
    TaskManager.mark_running(record.id)
    TaskManager.mark_failed(record.id, error_message="boom")
    assert pg.try_acquire_lease(record.id, worker="w1") is None
    assert _refresh(pg, record).status == TaskStatus.FAILED
    pg.update_status(record.id, TaskStatus.PENDING, progress="重试回队")
    assert pg.try_acquire_lease(record.id, worker="w1")
