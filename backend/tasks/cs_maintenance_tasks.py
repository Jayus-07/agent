"""tasks/cs_maintenance_tasks.py — 客服全局维护 beat 任务（P2.4）。

薄壳：核心逻辑全部在 backend/customer_service/maintenance.py
（与 Celery 解耦，可测试、可手动触发）。本模块只做任务注册与日志包装。

调度：celery_app.conf.beat_schedule 每 60s 两任务各跑一次；
全部动作走原子条件 UPDATE，重复执行/并发安全（幂等）。
"""
from __future__ import annotations

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="cs.handoff_timeout_scan")
def cs_handoff_timeout_scan() -> dict:
    """扫描超时未接入的转接 → closed（兜底 supervisor 运行时恢复）。"""
    from backend.customer_service.maintenance import scan_handoff_timeouts

    result = scan_handoff_timeouts()
    if not result.get("ok"):
        logger.error("[CSMaintenanceTask] handoff scan failed: %s", result.get("error"))
    return {"count": result.get("count", 0)}


@celery_app.task(name="cs.confirmation_expiry_scan")
def cs_confirmation_expiry_scan() -> dict:
    """扫描已过期的 pending 确认 → expired（兜底 confirmation_flow 过期分支）。"""
    from backend.customer_service.maintenance import scan_confirmation_expiries

    result = scan_confirmation_expiries()
    if not result.get("ok"):
        logger.error("[CSMaintenanceTask] confirmation scan failed: %s", result.get("error"))
    return {"count": result.get("count", 0)}


@celery_app.task(
    bind=True,
    name="cs.event_outbox_compensation",
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=CELERY_RETRY_BACKOFF,
    retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=CELERY_MAX_RETRIES,
)
def cs_event_outbox_compensation(self) -> dict:
    """PG 恢复后补偿 Redis Stream 中尚未落库的客服事件。"""
    from backend.customer_service.event_outbox import drain_pending_events

    result = drain_pending_events()
    if not result.get("ok"):
        raise RuntimeError(
            "客服事件补偿未完成："
            f"processed={result.get('processed', 0)} "
            f"failed={result.get('failed', 0)}"
        )
    return {
        "processed": result.get("processed", 0),
        "failed": result.get("failed", 0),
    }
