"""travel/booking/telemetry.py — Booking 可观测（STOP L7，任务书 §三十九/G35）

标签白名单（低基数）：provider / operation / status / from_state / to_state /
outcome / capability / reason。**禁止** order_id/quote_id/user_id/tenant_id/
email/name/provider_order_id 做 label（§三十九红线）。

全部软失败：遥测绝不影响 Booking 主链。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

travel_booking_requests_total = Counter(
    "travel_booking_requests_total",
    "Booking 操作请求总数（按 provider/operation/outcome）",
    labelnames=("provider", "operation", "outcome"),
)

travel_booking_state_transition_total = Counter(
    "travel_booking_state_transition_total",
    "订单状态转换计数（from_state/to_state；非法转换 fail_closed 单列）",
    labelnames=("from_state", "to_state"),
)

travel_booking_provider_calls_total = Counter(
    "travel_booking_provider_calls_total",
    "Provider 调用计数（outcome: succeeded/not_sent/rejected/unknown）",
    labelnames=("provider", "outcome"),
)

travel_booking_idempotency_total = Counter(
    "travel_booking_idempotency_total",
    "幂等账本结局（result: new/replay/conflict/uncertain/takeover）",
    labelnames=("provider", "result"),
)

travel_booking_in_doubt_total = Counter(
    "travel_booking_in_doubt_total",
    "IN_DOUBT 进入计数（进入与解除分 reason）",
    labelnames=("provider", "reason"),
)

travel_booking_reconciliation_total = Counter(
    "travel_booking_reconciliation_total",
    "对账执行计数（outcome: known_success/known_failure/safe_to_retry/in_doubt）",
    labelnames=("provider", "outcome"),
)

travel_booking_webhook_total = Counter(
    "travel_booking_webhook_total",
    "Webhook Inbox 计数（outcome: accepted/duplicate/quarantined/rejected）",
    labelnames=("provider", "outcome"),
)

travel_booking_recovery_total = Counter(
    "travel_booking_recovery_total",
    "恢复扫描动作计数（action: reconciled/resolved_booked/resolved_failed/requeued/manual）",
    labelnames=("provider", "action"),
)

travel_booking_latency_seconds = Histogram(
    "travel_booking_latency_seconds",
    "Booking 提交端到端耗时（秒，含幂等与 provider 调用）",
    labelnames=("provider",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0),
)


def record_request(provider: str, operation: str, outcome: str,
                   latency_ms: int = 0) -> None:
    try:
        travel_booking_requests_total.labels(
            provider=provider, operation=operation, outcome=outcome).inc()
        if latency_ms:
            travel_booking_latency_seconds.labels(
                provider=provider).observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def record_transition(from_state: str, to_state: str) -> None:
    try:
        travel_booking_state_transition_total.labels(
            from_state=from_state, to_state=to_state).inc()
    except Exception:  # noqa: BLE001
        pass


def record_transition_rejected(from_state: str, to_state: str) -> None:
    """非法转换（fail-closed 单列，G21 观测）。"""
    try:
        travel_booking_state_transition_total.labels(
            from_state=f"illegal:{from_state}", to_state=to_state).inc()
    except Exception:  # noqa: BLE001
        pass


def record_provider_call(provider: str, outcome: str) -> None:
    try:
        travel_booking_provider_calls_total.labels(
            provider=provider, outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def record_idempotency(provider: str, result: str) -> None:
    try:
        travel_booking_idempotency_total.labels(
            provider=provider, result=result).inc()
    except Exception:  # noqa: BLE001
        pass


def record_in_doubt(provider: str, reason: str) -> None:
    try:
        travel_booking_in_doubt_total.labels(
            provider=provider, reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


def record_reconciliation(provider: str, outcome: str) -> None:
    try:
        travel_booking_reconciliation_total.labels(
            provider=provider, outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def record_webhook(provider: str, outcome: str) -> None:
    try:
        travel_booking_webhook_total.labels(
            provider=provider, outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def record_recovery(provider: str, action: str) -> None:
    try:
        travel_booking_recovery_total.labels(
            provider=provider, action=action).inc()
    except Exception:  # noqa: BLE001
        pass
