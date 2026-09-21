"""tasks/task_manager.py — 任务管理门面（API 层 ↔ Celery ↔ Redis 控制面）。

职责：
- 入队/恢复/取消/暂停 的编排动作（不直接执行图）
- Redis 控制标志：cancel / pause（节点边界生效）
- Redis pub/sub 事件广播：task_executor 发布 → SSE 路由订阅

不 import LangGraph；不 import Skill 层。
"""
from __future__ import annotations

import json

from backend.config.tasks import (
    TASK_EVENT_CHANNEL,
    TASK_FLAG_TTL,
    TASK_KEY_PREFIX,
    TASK_ZOMBIE_THRESHOLD_SECONDS,
)
from backend.models.task import TaskRecord, TaskStatus
from backend.shared.logger import logger

_CANCEL_FLAG = TASK_KEY_PREFIX + "cancel:"
_PAUSE_FLAG = TASK_KEY_PREFIX + "pause:"


def _redis():
    """复用全局 Redis 单例；不可用返回 None（调用方优雅降级）。"""
    from backend.infra.redis.client import get_redis

    return get_redis()


# ═══════════════════════════════════════════════════
# 控制标志
# ═══════════════════════════════════════════════════

def request_cancel(task_id: str) -> bool:
    """置取消标志（Worker 在节点边界轮询生效）。返回是否落标志成功。"""
    r = _redis()
    if r is None:
        logger.error("[TaskManager] Redis 不可用，取消标志无法下发: %s", task_id)
        return False
    r.set(_CANCEL_FLAG + task_id, "1", ex=TASK_FLAG_TTL)
    # 仍在队列未开跑的任务：直接 revoke（不等 Worker 拾取）
    _revoke_queued(task_id)
    return True


def request_pause(task_id: str) -> bool:
    """置暂停标志：Worker 在下一个节点边界停止 → WAITING_USER/PAUSED。"""
    r = _redis()
    if r is None:
        return False
    r.set(_PAUSE_FLAG + task_id, "1", ex=TASK_FLAG_TTL)
    return True


def clear_flags(task_id: str) -> None:
    """恢复/重启前清除全部控制标志。"""
    r = _redis()
    if r is not None:
        r.delete(_CANCEL_FLAG + task_id, _PAUSE_FLAG + task_id)


def is_cancel_requested(task_id: str) -> bool:
    r = _redis()
    return bool(r and r.exists(_CANCEL_FLAG + task_id))


def is_pause_requested(task_id: str) -> bool:
    r = _redis()
    return bool(r and r.exists(_PAUSE_FLAG + task_id))


def _revoke_queued(task_id: str) -> None:
    """对已入队未执行的 Celery 任务发 revoke（温和模式，执行中无效）。"""
    from backend.services import task_service

    try:
        record = task_service.get_task(task_id)
        if record and record.celery_task_id:
            from backend.tasks.celery_app import celery_app

            celery_app.control.revoke(record.celery_task_id, terminate=False)
    except Exception:
        # revoke 失败不致命：标志位兜底（Worker 拾取后仍会取消）
        logger.debug("[TaskManager] revoke failed (flag 兜底): %s", task_id, exc_info=True)


# ═══════════════════════════════════════════════════
# 入队 / 恢复
# ═══════════════════════════════════════════════════

def enqueue_task(record: TaskRecord) -> str | None:
    """任务投递 Celery 队列；返回 celery async result id（不可用时 None）。"""
    from backend.services import task_service

    clear_flags(record.id)
    try:
        from backend.tasks.agent_tasks import execute_agent_task

        async_result = execute_agent_task.apply_async(
            args=[record.id], queue="agent")
        task_service.mark_queued(record.id, async_result.id, queue="agent")
        return async_result.id
    except Exception as e:
        # broker 不可达：任务留在 PENDING，由 API 返回 503 提示
        logger.error("[TaskManager] enqueue failed: %s (%s)", record.id, e)
        raise


def resume_task(task_id: str, user_input: str = "") -> TaskRecord:
    """恢复 WAITING_USER / PAUSED / FAILED 任务：

    1. 清控制标志 → 2. 若带用户输入则写入 DB（Worker 从 checkpoint 续跑时注入
    state）→ 3. 状态回 PENDING → 4. 重新入队。
    Worker 拾取时按 status/checkpoint 判定续跑，不重头执行图。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status not in TaskStatus.resumable():
        raise ValueError(f"task not resumable in status {record.status.value}")

    clear_flags(task_id)
    if user_input:
        task_service.append_checkpoint(
            task_id, "__user_input__", {"user_input": user_input})
    task_service.update_status(
        task_id, TaskStatus.PENDING,
        progress="等待重新调度（从 checkpoint 恢复）" if not user_input
        else "已注入用户输入，等待恢复执行")
    record = task_service.get_task(task_id)  # type: ignore[assignment]
    enqueue_task(record)  # type: ignore[arg-type]
    return task_service.get_task(task_id)  # type: ignore[return-value]


# ═══════════════════════════════════════════════════
# 僵尸任务收尸（2026-09-21 高并发审查 B5）
# ═══════════════════════════════════════════════════

def reconcile_zombie_tasks() -> dict:
    """beat 周期任务：把心跳停更超阈值的僵尸 RUNNING 任务收尸为 FAILED。

    场景：Worker 崩溃 + broker 消息丢失（Redis 逐出/未重投）→ 无 acks_late
    重投兜底，任务永久卡 RUNNING，admin retry 因非 resumable 被拒。
    收尸为 FAILED 后即可在管理端重试（从 checkpoint 续跑）。

    原子性：收尸走 task_service.reap_zombie_running 的条件 UPDATE，
    与活 Worker 并发安全；收尸后清控制标志 + 广播 SSE failed 事件
    （让还挂在任务详情页的 SSE 流正常收尾）。
    """
    from backend.services import task_service

    reaped = task_service.reap_zombie_running(
        threshold_seconds=TASK_ZOMBIE_THRESHOLD_SECONDS,
        status=TaskStatus.FAILED,
        error_message=(
            f"疑似 Worker 崩溃：心跳停更超过 {TASK_ZOMBIE_THRESHOLD_SECONDS}s，"
            "由 zombie reconcile 自动收尸（可重试）"),
        error_type="ZOMBIE_RECONCILED",
    )
    for task_id in reaped:
        clear_flags(task_id)
        publish_event(task_id, "failed",
                      message="Worker 心跳超时，任务被自动收尸（可重试）")
    if reaped:
        logger.warning("[TaskManager] zombie reconcile 收尸 %d 个任务: %s",
                       len(reaped), reaped)
    return {"ok": True, "count": len(reaped),
            "threshold_seconds": TASK_ZOMBIE_THRESHOLD_SECONDS}


def force_cancel_task(task_id: str) -> dict:
    """管理端强制撤销（B5：RUNNING 纳入强制取消范围）。

    1. 置取消标志 + 队列内 revoke（原有温和路径，活 Worker 节点边界生效）；
    2. 若任务为 RUNNING 且心跳已停更超阈值（僵尸），直接原子收尸为
       CANCELLED——不等一个永远不会来的 Worker。

    返回 {"flag": 标志是否下发成功, "forced": 是否强制收尸, "status": 最新状态}。
    任务不存在抛 LookupError；已终态抛 ValueError。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        raise ValueError(f"task already terminal: {record.status.value}")

    flag_ok = request_cancel(task_id)

    forced = False
    if record.status == TaskStatus.RUNNING:
        reaped = task_service.reap_zombie_running(
            threshold_seconds=TASK_ZOMBIE_THRESHOLD_SECONDS,
            status=TaskStatus.CANCELLED,
            error_message="管理员强制撤销（RUNNING 心跳超时，直接收尸）",
            error_type="ADMIN_FORCE_CANCEL",
            task_id=task_id,
        )
        forced = bool(reaped)
        if forced:
            clear_flags(task_id)
            publish_event(task_id, "cancelled",
                          message="管理员强制撤销（僵尸任务收尸）")

    record = task_service.get_task(task_id)
    return {"flag": flag_ok, "forced": forced,
            "status": record.status.value if record else ""}


# ═══════════════════════════════════════════════════
# 事件广播（SSE 数据源）
# ═══════════════════════════════════════════════════

def publish_event(task_id: str, event: str, **payload) -> None:
    """发布任务事件到 Redis pub/sub（SSE 路由订阅同一通道）。

    事件形态对齐验收协议：
      {"event": "node_start", "node": "planner", ...}
    广播失败不影响执行（fire-and-forget，SSE 是 best-effort 推送）。
    """
    r = _redis()
    if r is None:
        return
    try:
        r.publish(TASK_EVENT_CHANNEL + task_id,
                  json.dumps({"event": event, **payload},
                             ensure_ascii=False, default=str))
    except Exception:
        logger.debug("[TaskManager] publish event failed: %s/%s",
                     task_id, event, exc_info=True)


def subscribe_events(task_id: str):
    """订阅任务事件通道（SSE 路由用；返回 pubsub 或 None）。"""
    r = _redis()
    if r is None:
        return None
    try:
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(TASK_EVENT_CHANNEL + task_id)
        return pubsub
    except Exception:
        logger.debug("[TaskManager] subscribe failed: %s", task_id, exc_info=True)
        return None
