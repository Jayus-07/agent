"""tests/test_task_zombie_reconcile.py — 僵尸 RUNNING 任务收尸测试（审查 B5）。

覆盖：
  1. reap_zombie_running：超阈值 RUNNING → FAILED（可重试），附带 duration/error_type
  2. 心跳新鲜（未超阈值）的 RUNNING 不被误杀
  3. 原子性：已收尸任务不会被二次收尸（条件 UPDATE 幂等）
  4. reconcile_zombie_tasks：收尸后清控制标志 + 广播 SSE failed 事件
  5. force_cancel_task：僵尸 RUNNING → 强制 CANCELLED + forced=True
  6. force_cancel_task：活 RUNNING → 只下标志不收尸（forced=False）

策略：真实 agent_memory（不可达则 skip 整组）；Redis 用 fake（不依赖真 Redis）。
"""
from __future__ import annotations

import uuid

import pytest

from backend.models.task import TaskStatus


class _FakeRedis:
    """最小 Redis 桩：记录 set/delete/publish 调用。"""

    def __init__(self):
        self.sets: list[tuple[str, str]] = []
        self.deletes: list[list[str]] = []
        self.published: list[tuple[str, str]] = []

    def set(self, key, value, ex=None):
        self.sets.append((key, str(value)))

    def delete(self, *keys):
        self.deletes.append([str(k) for k in keys])

    def exists(self, key):
        return 0

    def publish(self, channel, message):
        self.published.append((str(channel), str(message)))


@pytest.fixture(scope="module")
def pg():
    """真实 agent_memory 连接；不可达则跳过整组用例。"""
    pytest.importorskip("psycopg")
    try:
        from backend.services import task_service

        task_service.ensure_schema()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"agent_memory 不可达，跳过僵尸任务测试: {e}")
    from backend.services import task_service

    return task_service


def _backdate_updated_at(pg, task_id: str, seconds: int = 7200) -> None:
    """把 updated_at 回拨，模拟 Worker 心跳停更（免真实等待）。"""
    with pg._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET updated_at = now() - (%s || ' seconds')::interval "
            "WHERE id = %s",
            (str(seconds), task_id),
        )


def _make_running_task(pg, *, backdate: bool) -> str:
    record = pg.create_task(f"zombie-test-{uuid.uuid4().hex[:8]}", "僵尸收尸测试")
    pg.update_status(record.id, TaskStatus.RUNNING, worker="test-worker")
    if backdate:
        _backdate_updated_at(pg, record.id)
    return record.id


@pytest.fixture()
def fake_redis(monkeypatch):
    from backend.tasks import task_manager

    fake = _FakeRedis()
    monkeypatch.setattr(task_manager, "_redis", lambda: fake)
    yield fake


# ═══════════════════════════════════════════════════
# 1-3: task_service.reap_zombie_running
# ═══════════════════════════════════════════════════

def test_reap_zombie_running_marks_failed(pg):
    task_id = _make_running_task(pg, backdate=True)
    reaped = pg.reap_zombie_running(threshold_seconds=3600)
    assert task_id in reaped
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error_type == "ZOMBIE_RECONCILED"
    # FAILED ∈ resumable()：admin retry 恢复可用（B5 的核心诉求）
    assert record.status in TaskStatus.resumable()
    assert record.duration_ms is not None  # started_at 非空 → 自动补算耗时


def test_reap_skips_fresh_running(pg):
    task_id = _make_running_task(pg, backdate=False)
    reaped = pg.reap_zombie_running(threshold_seconds=3600)
    assert task_id not in reaped
    assert pg.get_task(task_id).status == TaskStatus.RUNNING


def test_reap_is_atomic_no_double_reap(pg):
    task_id = _make_running_task(pg, backdate=True)
    first = pg.reap_zombie_running(threshold_seconds=3600)
    assert task_id in first
    # 第二轮：行已非 RUNNING → 条件 UPDATE 不再命中
    second = pg.reap_zombie_running(threshold_seconds=3600)
    assert task_id not in second


# ═══════════════════════════════════════════════════
# 4: task_manager.reconcile_zombie_tasks
# ═══════════════════════════════════════════════════

def test_reconcile_clears_flags_and_publishes(pg, fake_redis):
    import json

    from backend.config.tasks import TASK_EVENT_CHANNEL
    from backend.tasks import task_manager

    task_id = _make_running_task(pg, backdate=True)
    result = task_manager.reconcile_zombie_tasks()
    assert result["ok"] is True
    assert result["count"] >= 1
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.FAILED
    # 清控制标志（cancel/pause key 会被 delete）
    assert any("cancel" in k or "pause" in k
               for keys in fake_redis.deletes for k in keys)
    # 广播 SSE failed 事件到该任务的 events 通道（对齐 SSE 终态事件名）
    published = [m for c, m in fake_redis.published
                 if c == TASK_EVENT_CHANNEL + task_id]
    assert any(json.loads(m).get("event") == "failed" for m in published)


# ═══════════════════════════════════════════════════
# 5-6: task_manager.force_cancel_task
# ═══════════════════════════════════════════════════

def test_force_cancel_zombie_running(pg, fake_redis):
    from backend.tasks import task_manager

    task_id = _make_running_task(pg, backdate=True)
    result = task_manager.force_cancel_task(task_id)
    assert result["flag"] is True
    assert result["forced"] is True
    assert result["status"] == TaskStatus.CANCELLED.value
    record = pg.get_task(task_id)
    assert record.status == TaskStatus.CANCELLED
    assert record.error_type == "ADMIN_FORCE_CANCEL"


def test_force_cancel_live_running_only_flag(pg, fake_redis):
    from backend.tasks import task_manager

    task_id = _make_running_task(pg, backdate=False)
    result = task_manager.force_cancel_task(task_id)
    assert result["flag"] is True
    assert result["forced"] is False
    assert result["status"] == TaskStatus.RUNNING.value
    # 取消标志已下发（活 Worker 在节点边界消费）
    assert any("cancel" in k for keys in fake_redis.sets for k in keys)
    assert pg.get_task(task_id).status == TaskStatus.RUNNING


def test_force_cancel_terminal_rejected(pg):
    import pytest as _pytest

    from backend.tasks import task_manager

    record = pg.create_task(f"zombie-test-{uuid.uuid4().hex[:8]}", "终态拒绝测试")
    pg.update_status(record.id, TaskStatus.SUCCESS, output={"done": True})
    with _pytest.raises(ValueError):
        task_manager.force_cancel_task(record.id)


def test_force_cancel_unknown_task(pg):
    import pytest as _pytest

    from backend.tasks import task_manager

    with _pytest.raises(LookupError):
        task_manager.force_cancel_task(str(uuid.uuid4()))
