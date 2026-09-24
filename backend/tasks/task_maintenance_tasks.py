"""tasks/task_maintenance_tasks.py — 任务体系维护 beat 任务（审查 B5 + Phase2 Step1）。

薄壳：核心逻辑全部在 backend/tasks/task_manager.py（可手动触发）：
- stale_execution_recovery → sweep_stale_executions（Phase2 Step1 主恢复路径）
- zombie_reconcile → reconcile_zombie_tasks（最终兜底，详见其 docstring）

调度（celery_app.conf.beat_schedule）：
- tasks-stale-execution-recovery 每 TASK_RECOVERY_SWEEP_INTERVAL 秒（默认 30s）：
  租约过期的 RUNNING → 原子认领 → 按原 queue 自动重投 → checkpoint 续跑
- tasks-zombie-reconcile 每 TASK_ZOMBIE_RECONCILE_INTERVAL 秒（默认 300s）：
  心跳停更超 TASK_ZOMBIE_THRESHOLD_SECONDS 的残留 RUNNING → FAILED
  （Phase2 起只在 sweep 链路失效时兜底）

为什么还需要 zombie：sweep 依赖 beat→队列→worker 链路自身可用；该链路
整体故障时无任何自动恢复，只能靠本任务定期收尸为 FAILED 保住可重试性。
"""
from __future__ import annotations

from backend.config.tasks import (
    TASK_RECOVERY_SWEEP_INTERVAL,
    TASK_ZOMBIE_RECONCILE_INTERVAL,
    TASK_ZOMBIE_THRESHOLD_SECONDS,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="tasks.stale_execution_recovery")
def stale_execution_recovery() -> dict:
    """扫描租约过期的 RUNNING 任务 → 原子认领 → 按原 queue 自动重投恢复。"""
    from backend.tasks.task_manager import sweep_stale_executions

    result = sweep_stale_executions()
    if not result.get("ok"):
        logger.error("[TaskMaintenance] stale recovery sweep failed: %s", result)
    return result


@celery_app.task(name="tasks.zombie_reconcile")
def zombie_reconcile() -> dict:
    """扫描心跳停更超阈值的僵尸 RUNNING 任务 → 原子收尸为 FAILED（最终兜底）。"""
    from backend.tasks.task_manager import reconcile_zombie_tasks

    result = reconcile_zombie_tasks()
    if not result.get("ok"):
        logger.error("[TaskMaintenance] zombie reconcile failed: %s", result)
    return result


@celery_app.task(name="tasks.pending_recovery")
def pending_recovery() -> dict:
    """stale PENDING 派发恢复（Phase3 STOP B：delivery recovery accelerator）。

    只恢复 delivery 不执行 workflow：CAS 认领 → 经 dispatch_task（QueueRouter）
    重投 → 正常 worker pickup（terminal 短路 → lease → admission）。
    """
    from backend.tasks.pending_recovery import recover_stale_pending_tasks

    result = recover_stale_pending_tasks()
    if not result.get("ok"):
        logger.error("[TaskMaintenance] pending recovery failed: %s", result)
    return result


@celery_app.task(name="tasks.idempotency_retention")
def idempotency_retention() -> dict:
    """幂等 ledger 保留策略（Phase2 Step6 §五十二）。

    只删除显式设置过 expires_at 且已过期的记录（低风险通知类）；
    业务动作类记录默认不写 expires_at = 永久保留——TTL 到期把不可逆
    动作重新放行是最危险路径。stale RUNNING claim 是 IN_DOUBT 裁决
    对象，绝不在此清理。
    """
    from backend.shared.idempotency import purge_expired_idempotency_records

    try:
        deleted = purge_expired_idempotency_records()
    except Exception as exc:  # noqa: BLE001 — 维护任务失败不外抛，观测可见
        logger.error("[TaskMaintenance] idempotency retention failed: %s", exc)
        return {"ok": False, "deleted": 0, "error": str(exc)}
    if deleted:
        logger.info("[TaskMaintenance] idempotency retention deleted=%d", deleted)
    return {"ok": True, "deleted": deleted}


__all__ = ["stale_execution_recovery", "zombie_reconcile", "idempotency_retention",
           "TASK_RECOVERY_SWEEP_INTERVAL",
           "TASK_ZOMBIE_RECONCILE_INTERVAL", "TASK_ZOMBIE_THRESHOLD_SECONDS"]
