"""tasks/travel_booking_tasks.py — Booking 维护任务（STOP L5/L6）

travel.booking_recovery_scan：确认过期兜底 + stale SUBMITTING/IN_DOUBT
按 Provider 能力收敛（Model A 同 key 重放 / B 先对账 / C 保持人工）。
全部动作经状态机白名单与幂等账本，任务重复投递无害（幂等）。

失败分类：瞬时 DB 抖动 → 可重试（countdown 吸收）；扫描器自身幂等，
重跑不会重复收敛。
"""
from __future__ import annotations

from backend.tasks.celery_app import celery_app


@celery_app.task(
    name="travel.booking_recovery_scan",
    bind=True,
    max_retries=1,
    acks_late=True,
)
def travel_booking_recovery_scan(self) -> dict:
    from backend.shared.logger import logger

    from backend.travel.booking.recovery import run_recovery_scan

    try:
        stats = run_recovery_scan()
    except Exception as exc:  # noqa: BLE001 — 扫描失败按可重试上抛（error_taxonomy 统一分类）
        logger.error("[BookingRecovery] 扫描失败", exc_info=True)
        raise self.retry(exc=exc, countdown=60) from exc
    return stats
