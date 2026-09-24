"""travel/booking/recovery.py — 恢复扫描（STOP L5，G19 W1-W3 / §二十六）

beat 周期任务体（travel.booking_recovery_scan → maintenance 队列）：
  1. 确认过期扫描：awaiting_confirmation 且 Quote TTL 已到 → EXPIRED
     （响应式过期之外的兜底；不要求用户重新点击）；
  2. stale SUBMITTING / IN_DOUBT → 按 provider 能力收敛：
     Model A（SAFE_RETRY）→ 重入 executor（同 key 重放，provider 去重）；
     Model B（RECONCILE_FIRST）→ reconcile_order（先查再定）；
     Model C（IN_DOUBT）→ 仅对账可用则对账，否则保持（人工通道）。

全部动作经状态机/幂等账本白名单，恢复自身重复执行无害（幂等）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.travel.booking.reconciliation import reconcile_order
from backend.travel.booking.state import BookingOrderStatus
from backend.travel.booking.store import BookingStore
from backend.travel.booking.telemetry import record_recovery


def run_recovery_scan(*, store: BookingStore | None = None,
                      provider_factory=None,
                      stale_seconds: int | None = None,
                      batch: int | None = None,
                      ledger_table: str = "ai.idempotency_records",
                      conn_factory=None) -> dict:
    """扫描并收敛一轮；返回动作统计（可观测/测试断言）。"""
    from backend.config.travel_booking import (
        TRAVEL_BOOKING_PROVIDER,
        TRAVEL_BOOKING_RECOVERY_BATCH,
        TRAVEL_BOOKING_RECOVERY_STALE_SECONDS,
    )
    from backend.travel.booking.provider_capabilities import resolve_provider

    store = store or BookingStore(conn_factory)
    stale_seconds = stale_seconds if stale_seconds is not None \
        else TRAVEL_BOOKING_RECOVERY_STALE_SECONDS
    batch = batch or TRAVEL_BOOKING_RECOVERY_BATCH
    stats = {"expired": 0, "reconciled": 0, "released": 0, "booked": 0,
             "kept_in_doubt": 0, "skipped": 0}

    # 1) 确认过期兜底
    for order in store.expired_awaiting(limit=batch):
        try:
            store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"],
                target=BookingOrderStatus.EXPIRED.value,
                cause="confirmation_expired", actor="recovery",
                event_type="booking_confirmation_expired",
                failure_code="QUOTE_EXPIRED", failure_class="business")
            stats["expired"] += 1
        except Exception:  # noqa: BLE001 — 并发抢先即收敛，单条不拖垮扫描
            pass

    # 2) stale SUBMITTING / IN_DOUBT
    if provider_factory is not None:
        provider, contract = provider_factory()
    else:
        from backend.config.travel_booking import TRAVEL_BOOKING_PROVIDER as _cfg_provider

        resolved = resolve_provider(_cfg_provider)
        if resolved is None:
            stats["skipped"] += 1
            return stats
        provider, contract = resolved

    stale = store.stale_orders(
        statuses=[BookingOrderStatus.SUBMITTING.value,
                  BookingOrderStatus.IN_DOUBT.value],
        older_than_seconds=stale_seconds, limit=batch)
    for order in stale:
        try:
            # Model A：先尝试直接重入执行器（同 key 重放安全；IN_DOUBT 单
            # 除外——IN_DOUBT 只能对账/人工，不得自动重试）
            if (order["status"] == BookingOrderStatus.SUBMITTING.value
                    and contract.unknown_result_policy.value == "safe_retry"):
                from backend.travel.booking.executor import BookingExecutor

                outcome = BookingExecutor(
                    store, provider, contract,
                    ledger_table=ledger_table, conn_factory=conn_factory,
                ).execute(
                    order_id=order["order_id"],
                    tenant_id=order["tenant_id"], user_id=order["user_id"],
                    owner_execution_id="recovery")
                record_recovery(provider.name,
                                "requeued" if outcome.result == "in_progress"
                                else outcome.result)
                stats["reconciled"] += 1
                if outcome.result == "booked":
                    stats["booked"] += 1
                continue

            # Model B / IN_DOUBT 订单：对账
            outcome = reconcile_order(store=store, provider=provider,
                                      contract=contract, order=order,
                                      conn_factory=conn_factory,
                                      ledger_table=ledger_table)
            if outcome.result == "booked":
                stats["booked"] += 1
            elif outcome.result == "released_for_retry":
                stats["released"] += 1
            elif outcome.result == "failed":
                stats["reconciled"] += 1
            else:
                stats["kept_in_doubt"] += 1
            record_recovery(provider.name, outcome.result)
        except Exception:  # noqa: BLE001 — 单条失败不拖垮整批扫描
            logger.warning("[BookingRecovery] 订单 %s 收敛失败",
                           order["order_id"], exc_info=True)
    logger.info("[BookingRecovery] 扫描完成 %s", stats)
    return stats
