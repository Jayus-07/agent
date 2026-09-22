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


def pause_task(task_id: str) -> dict:
    """暂停任务（Phase1 Step4 编排入口，幂等）。

    语义：不强杀当前原子节点——RUNNING 中请求暂停时，Worker 在当前节点
    完成并落库后、下一节点开始前发现标志，停止调度后续节点（C 不得开始），
    checkpoint 保留指向最后成功节点。

    - RUNNING：置 Redis 暂停标志（节点边界生效）
    - PENDING（未拾取）：直接原子落库 PAUSED（mark_paused_if_pending），
      不依赖 Worker；竞态失败（恰好被拾取）回落标志路径
    - PAUSED：幂等返回，不重复置标志
    - 终态：拒绝（ValueError）
    返回 {"ok": bool, "already": 是否原本已暂停, "mode": 标志/落库, ...}。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        raise ValueError(f"task already terminal: {record.status.value}")
    if record.status == TaskStatus.PAUSED:
        return {"ok": True, "already": True, "status": "PAUSED"}

    if record.status == TaskStatus.PENDING:
        if task_service.mark_paused_if_pending(task_id):
            publish_event(task_id, "paused", node="queued",
                          message="任务在队列中暂停")
            return {"ok": True, "already": False, "status": "PAUSED",
                    "mode": "queued"}
        # 竞态：API 读到 PENDING 后 Worker 恰好拾取（租约已置 RUNNING）
        # → 回落标志路径，Worker 在第一个节点边界停下
        record = task_service.get_task(task_id)
        if record is not None and record.status == TaskStatus.RUNNING:
            ok = request_pause(task_id)
            return {"ok": ok, "already": False, "status": "RUNNING",
                    "mode": "flag", "flag": ok}
        raise ValueError("task status changed during pause, retry")

    ok = request_pause(task_id)
    if not ok:
        logger.error("[TaskManager] Redis 不可用，暂停标志无法下发: %s", task_id)
        return {"ok": False, "already": False, "status": record.status.value,
                "mode": "flag", "flag": False}
    return {"ok": True, "already": False, "status": record.status.value,
            "mode": "flag", "flag": True}


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


def cancel_task(task_id: str) -> dict:
    """取消任务（Phase1 Step6 编排入口，幂等）。

    语义：不强杀当前原子节点——RUNNING 中取消时 Worker 在当前节点完成后
    的边界停下，后续节点不得开始；checkpoint 与已完成结果保留；
    CANCELLED 为终态，resume 被明确拒绝。

    - PENDING / PAUSED / WAITING_USER：原子落库 CANCELLED（不经 Worker），
      并对队列内消息发 revoke；竞态（恰好被拾取）回落标志路径
    - RUNNING：置 Redis 取消标志（节点边界生效），Worker 捕获后落 CANCELLED
    - 终态（含重复 cancel）：幂等返回 already

    返回 {"ok", "already", "status", "mode": db|flag}。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    if record.status.is_terminal():
        return {"ok": True, "already": True, "status": record.status.value}

    # 队列内消息直接失效（已在执行则 revoke 无效，幂等无害）
    _revoke_queued(task_id)

    if record.status in (TaskStatus.PENDING, TaskStatus.PAUSED,
                         TaskStatus.WAITING_USER):
        if task_service.mark_cancelled_if_status(task_id, record.status):
            clear_flags(task_id)
            publish_event(task_id, "cancelled",
                          node=record.current_node or "queued",
                          message="任务已取消")
            return {"ok": True, "already": False, "status": "CANCELLED",
                    "mode": "db"}
        # 竞态：读状态后恰好被拾取/恢复 → 回落标志路径
        record = task_service.get_task(task_id)
        if record is None:
            raise LookupError(f"task not found: {task_id}")
        if record.status.is_terminal():
            return {"ok": True, "already": True,
                    "status": record.status.value}

    ok = request_cancel(task_id)
    if not ok:
        logger.error("[TaskManager] Redis 不可用，取消标志无法下发: %s", task_id)
        return {"ok": False, "already": False,
                "status": record.status.value, "mode": "flag"}
    return {"ok": True, "already": False, "status": record.status.value,
            "mode": "flag"}


def resume_task(task_id: str, user_input: str = "",
                *, allow_failed: bool = False) -> TaskRecord:
    """恢复任务（Phase1 Step5：原子认领，重复 resume 不产生第二个执行链）。

    1. 清控制标志 → 2. 若带用户输入写入 DB（Worker 续跑时注入 state）→
    3. 原子认领 expected→PENDING（条件 UPDATE，并发双 resume 恰一个胜出，
    败者幂等返回不重复入队）→ 4. 入队。Worker 拾取后按租约 + checkpoint
    判定续跑，不重头执行图；真正执行权由 try_acquire_lease 仲裁
    （max_concurrent_executor_per_task=1）。

    - 默认仅 PAUSED / WAITING_USER 可恢复（Phase1 规格口径；
      WAITING_USER 为"暂停等人"变体，既有产品能力，登记例外）
    - FAILED 重试走管理端通道（allow_failed=True），保持自愈式续跑语义
    - 入队失败回滚 PENDING→PAUSED（状态机合法），任务保留可再次 resume

    返回最新 TaskRecord（败者返回的是已被对手认领后的行，status=PENDING）。
    """
    from backend.services import task_service

    record = task_service.get_task(task_id)
    if record is None:
        raise LookupError(f"task not found: {task_id}")
    status = record.status
    if status in (TaskStatus.SUCCESS, TaskStatus.CANCELLED):
        raise ValueError(f"task is terminal ({status.value}), resume rejected")
    if status == TaskStatus.FAILED and not allow_failed:
        raise ValueError(
            "仅 PAUSED/WAITING_USER 可恢复；FAILED 重试请走管理端重试通道")
    if status in (TaskStatus.RUNNING, TaskStatus.PENDING):
        # 已在执行/排队：视为幂等命中（不重复入队）
        logger.info("[TaskManager] resume on %s task %s, skip re-enqueue",
                    status.value, task_id)
        return record

    clear_flags(task_id)
    if user_input:
        task_service.append_checkpoint(
            task_id, "__user_input__", {"user_input": user_input})
    if not task_service.claim_for_resume(task_id, status):
        # 并发对手已认领（status 已变）：幂等返回最新行，不重复入队
        logger.info("[TaskManager] resume lost race for %s (already claimed),"
                    " skip re-enqueue", task_id)
        return task_service.get_task(task_id)  # type: ignore[return-value]

    record = task_service.get_task(task_id)  # type: ignore[assignment]
    try:
        enqueue_task(record)  # type: ignore[arg-type]
    except Exception:
        # 入队失败回滚到 PAUSED（PENDING→PAUSED 白名单合法），可再次 resume
        task_service.mark_paused_if_pending(
            task_id, progress="入队失败已回滚，可再次恢复")
        raise
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
