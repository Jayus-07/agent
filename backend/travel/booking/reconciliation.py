"""travel/booking/reconciliation.py — 对账（STOP L5，G30：查询事实 ≠ 重试）

Reconciliation 做「查询事实」；Retry 做「再次执行操作」。两者严格分离：
  IN_DOUBT / stale SUBMITTING 订单 → provider lookup（按 merchant ref）→
    订单已存在        → BOOKED（provider_order_id 落库）
    明确不存在且 lookup authoritative → 才允许解除阻断（ledger 标
    not_executed → 可安全重入 executor）或 FAILED
    无法判断（查询不可用/不支持）→ 保持 IN_DOUBT（不猜）

对账通道按 provider 能力账决定（fake_booking_bare 无 lookup → 只能人工，
走 shared.idempotency.resolve_stale_side_effect 人工裁决语义）。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.providers.travel.booking.contracts import LookupUnsupported
from backend.shared.idempotency import resolve_side_effect
from backend.shared.provider_idempotency import reconcile_provider_effect
from backend.travel.booking.state import BookingOrderStatus
from backend.travel.booking.store import StaleTransition, BookingStore
from backend.travel.booking.telemetry import (
    record_in_doubt,
    record_reconciliation,
)


@dataclass
class ReconcileOutcome:
    result: str          # booked | failed | released_for_retry | still_in_doubt | unchanged
    order: dict
    detail: str = ""


def reconcile_order(*, store: BookingStore, provider, contract,
                    order: dict, actor: str = "reconciliation",
                    conn_factory=None,
                    ledger_table: str = "ai.idempotency_records") -> ReconcileOutcome:
    """对一张 IN_DOUBT / stale SUBMITTING 订单做事实查询与收敛。"""
    if order["status"] not in (BookingOrderStatus.IN_DOUBT.value,
                               BookingOrderStatus.SUBMITTING.value):
        return ReconcileOutcome("unchanged", order,
                                f"状态 {order['status']} 无需对账")

    record_reconciliation(provider.name, "started")
    try:
        verdict = reconcile_provider_effect(
            contract=contract,
            lookup=lambda: provider.lookup_booking(order["merchant_order_id"]))
    except LookupUnsupported:
        verdict = None

    if verdict is None:
        # 不支持查询：保持 IN_DOUBT（人工裁决通道不变）
        record_reconciliation(provider.name, "in_doubt")
        return ReconcileOutcome("still_in_doubt", order,
                                "provider 不支持状态查询，等待人工对账")

    if verdict.value == "known_success":
        order = _safe_transition(
            store, order, BookingOrderStatus.BOOKED.value,
            cause="reconciliation", event_type="reconciliation_resolved",
            provider_order_id=None)
        record_reconciliation(provider.name, "known_success")
        return ReconcileOutcome("booked", order)
    if verdict.value == "known_failure":
        order = _safe_transition(
            store, order, BookingOrderStatus.FAILED.value,
            cause="reconciliation", event_type="reconciliation_resolved",
            failure_code="PROVIDER_FAILED", failure_class="unknown")
        record_reconciliation(provider.name, "known_failure")
        return ReconcileOutcome("failed", order)
    if verdict.value == "not_found_safe_to_retry":
        # Provider 契约保证权威「不存在」→ 解除账本保守阻断（IN_DOUBT 双
        # 形态自动分流：Model C 的 failed+UNCERTAIN 与崩溃窗 stale-running），
        # 订单回 FAILED(not_sent)（可安全重入 executor 重新走全部门）
        resolve_side_effect(
            tenant_id=order["tenant_id"], actor_id=order["user_id"],
            operation="travel.booking.create",
            client_key=order["merchant_order_id"],
            decision="not_executed", reason="reconciled_not_found",
            connection_factory=conn_factory, table=ledger_table,
        )
        order = _safe_transition(
            store, order, BookingOrderStatus.FAILED.value,
            cause="reconciliation", event_type="reconciliation_resolved",
            failure_code="NOT_SENT", failure_class="not_sent")
        record_reconciliation(provider.name, "safe_to_retry")
        return ReconcileOutcome("released_for_retry", order,
                                "provider 确认未创建，可安全重试")
    record_reconciliation(provider.name, "in_doubt")
    return ReconcileOutcome("still_in_doubt", order, "查询不可用，保持 IN_DOUBT")


def manual_resolve(*, store: BookingStore, order: dict, decision: str,
                   provider_order_id: str | None = None,
                   actor: str = "admin", reason: str = "manual_resolution",
                   conn_factory=None,
                   ledger_table: str = "ai.idempotency_records") -> dict:
    """人工裁决（Model C 唯一出口；decision: executed|not_executed）。

    ledger 侧经 resolve_side_effect 双形态分流收口（STOP E：Model C 的
    IN_DOUBT 主形态是 failed+UNCERTAIN，旧的 stale-running 专用通道对它
    静默 no-op）；订单状态经状态机（IN_DOUBT 出口 cause=manual，白名单
    唯一放行路径）。reason 透传裁决依据（须含 operator 标识）。
    """
    if decision not in ("executed", "not_executed"):
        raise ValueError("decision 必须是 executed / not_executed")
    resolve_side_effect(
        tenant_id=order["tenant_id"], actor_id=order["user_id"],
        operation="travel.booking.create",
        client_key=order["merchant_order_id"],
        decision=decision, reason=reason,
        connection_factory=conn_factory, table=ledger_table,
    )
    target = BookingOrderStatus.BOOKED if decision == "executed" \
        else BookingOrderStatus.FAILED
    record_in_doubt(order["provider"], f"manual_{decision}")
    return _safe_transition(
        store, order, target.value, cause="manual",
        event_type="reconciliation_resolved",
        provider_order_id=provider_order_id,
        failure_code=None if decision == "executed" else "MANUAL_NOT_EXECUTED",
        failure_class=None if decision == "executed" else "unknown")


def _safe_transition(store: BookingStore, order: dict, target: str, *,
                     cause: str, event_type: str,
                     provider_order_id: str | None = None,
                     failure_code: str | None = None,
                     failure_class: str | None = None) -> dict:
    try:
        return store.transition_order(
            order_id=order["order_id"],
            expected_status=order["status"], target=target,
            cause=cause, actor="reconciliation", event_type=event_type,
            failure_code=failure_code, failure_class=failure_class,
            provider_order_id=provider_order_id)
    except StaleTransition:
        return store.get_order(order["order_id"]) or order
