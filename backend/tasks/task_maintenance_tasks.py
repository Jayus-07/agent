"""tasks/task_maintenance_tasks.py — 任务体系维护 beat 任务（审查 B5）。

薄壳：核心逻辑全部在 backend/tasks/task_manager.py（reconcile_zombie_tasks，
原子条件 UPDATE，可手动触发）。本模块只做任务注册。

调度：celery_app.conf.beat_schedule 每 TASK_ZOMBIE_RECONCILE_INTERVAL 秒
跑一次（默认 300s）；判定阈值 TASK_ZOMBIE_THRESHOLD_SECONDS（默认硬超时+60s），
两个配置都在 backend/config/tasks.py。

为什么需要：acks_late + task_reject_on_worker_lost 只覆盖「broker 消息还在」
的重投；Redis 逐出消息（旧 allkeys-lru）或 visibility timeout 前消息丢失时，
无 Worker 认领，任务永久卡 RUNNING——admin retry 因非 resumable 被拒，
只能靠本任务定期收尸为 FAILED 恢复可重试性。
"""
from __future__ import annotations

from backend.config.tasks import (
    TASK_ZOMBIE_RECONCILE_INTERVAL,
    TASK_ZOMBIE_THRESHOLD_SECONDS,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="tasks.zombie_reconcile")
def zombie_reconcile() -> dict:
    """扫描心跳停更超阈值的僵尸 RUNNING 任务 → 原子收尸为 FAILED。"""
    from backend.tasks.task_manager import reconcile_zombie_tasks

    result = reconcile_zombie_tasks()
    if not result.get("ok"):
        logger.error("[TaskMaintenance] zombie reconcile failed: %s", result)
    return result


__all__ = ["zombie_reconcile", "TASK_ZOMBIE_RECONCILE_INTERVAL",
           "TASK_ZOMBIE_THRESHOLD_SECONDS"]
