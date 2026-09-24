"""travel/booking/executor.py — 幂等 Booking 执行器（STOP L4/L5，核心）

**核心目标（任务书 §一）**：同一业务意图最多执行一次外部 Booking 副作用；
无法证明成功也无法证明失败时必须 IN_DOUBT——绝不猜 SUCCESS，绝不盲目
重复下单。

执行前门（§二十一，顺序固定）：
  authorization → quote 有效 → confirmation 有效 → 价格/库存复核 →
  幂等 claim（shared/idempotency，PG 权威）→ 订单状态 CAS（SUBMITTING）
  之后才 provider.create_booking()。

Provider 结局处置（复用 Phase3 STOP C 冻结决策矩阵）：
  SUCCEEDED → complete ledger → 订单 BOOKED（同事务事件）
  NOT_SENT  → ledger FAILED（可重入）→ 订单 FAILED(not_sent)
  REJECTED  → ledger FAILED → 订单 FAILED(rejected)
  UNKNOWN   → 按能力三模型：A=同 key 重试安全 / B=先对账 / C=IN_DOUBT
              （本执行器内 A/B/C 的 UNKNOWN 一律 SideEffectOutcomeUnknown →
              订单 IN_DOUBT；A 的安全重试由接管策略+恢复扫描实现）

崩溃窗口：W1/W3 由 ledger lease + takeover_allowed（仅 Model A）+
恢复扫描（Model B 先对账 / Model C 人工）收敛——见 reconciliation.py。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.shared.idempotency import (
    IdempotencyUnavailable,
    SideEffectOutcomeUnknown,
)
from backend.shared.provider_idempotency import (
    ProviderContractMissing,
    ProviderEffectError,
    ProviderEffectOutcome,
    decide_outcome_action,
    derive_provider_key,
    ensure_execution_allowed,
    raise_for_decision,
)
from backend.travel.booking import revalidate
from backend.travel.booking.state import BookingOrderStatus, require_transition
from backend.travel.booking.store import StaleTransition, BookingStore

OPERATION = "travel.booking.create"

# 执行结局（渲染层话术映射在 reporter）
BOOKED = "booked"
ALREADY_BOOKED = "already_booked"
FAILED = "failed"
IN_DOUBT = "in_doubt"
PRICE_CHANGED = "price_changed"
SOLD_OUT = "sold_out"
OFFER_GONE = "offer_gone"
REVALIDATION_UNAVAILABLE = "revalidation_unavailable"
NOT_CONFIRMABLE = "not_confirmable"
CONFIRMATION_EXPIRED = "confirmation_expired"
IN_PROGRESS = "in_progress"

# UNKNOOWN 结局按能力三模型映射（冻结矩阵）→ 本执行器统一保守；
# Model A 的「安全重试」不是立即重发，而是接管策略 + 恢复扫描同 key 重放
_TAKEOVER_SAFE = "safe_retry"


@dataclass
class ExecutionOutcome:
    result: str
    order: dict
    new_quote: dict | None = None      # price_changed 时的替代 Quote
    detail: str = ""


def takeover_allowed_for(contract) -> bool:
    """Model A（native 幂等，SAFE_RETRY）才允许接管 stale claim 重放同 key；
    B/C 必须先对账/人工（recovery 扫描处理），绝不盲目重发。"""
    return contract.unknown_result_policy.value == _TAKEOVER_SAFE


class BookingExecutor:
    def __init__(self, store: BookingStore, provider, contract,
                 *, ledger_table: str = "ai.idempotency_records",
                 conn_factory=None):
        self.store = store
        self.provider = provider
        self.contract = contract
        self._ledger_table = ledger_table
        self._conn_factory = conn_factory

    # ────────────────────────────────────────────────
    def execute(self, *, order_id: str, tenant_id: str, user_id: str,
                owner_execution_id: str = "") -> ExecutionOutcome:
        from backend.travel.booking.authorization import assert_owns_order

        order = self.store.get_order(order_id)
        if order is None:
            return ExecutionOutcome(NOT_CONFIRMABLE, order={},
                                    detail="订单不存在")
        assert_owns_order(order, tenant_id=tenant_id, user_id=user_id)

        if order["status"] == BookingOrderStatus.BOOKED.value:
            return ExecutionOutcome(ALREADY_BOOKED, order=order,
                                    detail="订单已预订（幂等收口，W4）")
        if order["status"] == BookingOrderStatus.IN_DOUBT.value:
            # §二十四：不确定结果禁止自动重试；只能经 reconciliation/人工
            return ExecutionOutcome(IN_DOUBT, order=order,
                                    detail="供应商结果待核实，系统不会自动重复提交")
        reentry = order["status"] == BookingOrderStatus.SUBMITTING.value
        if not reentry and order["status"] != BookingOrderStatus.CONFIRMED.value:
            return ExecutionOutcome(NOT_CONFIRMABLE, order=order,
                                    detail=f"订单状态 {order['status']} 不可提交")

        quote = self.store.get_quote(order["quote_id"])
        if quote is None:
            return ExecutionOutcome(NOT_CONFIRMABLE, order=order,
                                    detail="Quote 不存在")
        if quote["status"] != "active":
            return ExecutionOutcome(NOT_CONFIRMABLE, order=order,
                                    detail=f"Quote 已 {quote['status']}")
        # 订单与 Quote 金额/币种一致性（服务端事实互检，Decimal 精确比较）：
        # 不一致 = 确认的事实已被破坏 → 阻断并要求重新确认（G9/G32）
        from decimal import Decimal

        if (Decimal(str(order["amount"])) != Decimal(str(quote["price_amount"]))
                or (order["currency"] or "").upper()
                != (quote["currency"] or "").upper()):
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"],
                target=BookingOrderStatus.FAILED.value,
                cause="price_changed", actor=user_id,
                event_type="booking_blocked_price_changed",
                failure_code="PRICE_CHANGED", failure_class="business",
                detail={"detail": "订单与报价金额不一致"})
            self.store.mark_quote_status(quote["quote_id"], "superseded")
            return ExecutionOutcome(
                PRICE_CHANGED, order=order,
                detail="订单与报价金额不一致，需要重新确认")
        if _quote_expired(quote):
            order = self._transition_tolerant(
                order, BookingOrderStatus.EXPIRED, cause="confirmation_expired",
                event_type="booking_confirmation_expired",
                failure_code="QUOTE_EXPIRED", failure_class="business")
            return ExecutionOutcome(CONFIRMATION_EXPIRED, order=order,
                                    detail="报价已过期")

        # ── P0 Gate：价格/库存复核（G9/G10）──────────────
        rv = revalidate.revalidate_quote(quote)
        if rv.result == revalidate.PRICE_CHANGED:
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"],
                target=BookingOrderStatus.FAILED.value,
                cause="price_changed", actor=user_id,
                event_type="booking_blocked_price_changed",
                failure_code="PRICE_CHANGED", failure_class="business",
                detail={"detail": rv.detail})
            new_quote = _supersede_and_requote(self.store, quote, rv)
            return ExecutionOutcome(
                PRICE_CHANGED, order=order, new_quote=new_quote,
                detail="价格发生变化，需要重新确认")
        if rv.result == revalidate.SOLD_OUT:
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"],
                target=BookingOrderStatus.FAILED.value,
                cause="sold_out", actor=user_id,
                event_type="booking_blocked_sold_out",
                failure_code="SOLD_OUT", failure_class="business")
            return ExecutionOutcome(SOLD_OUT, order=order, detail=rv.detail)
        if rv.result in (revalidate.OFFER_GONE, revalidate.UNAVAILABLE):
            code = "OFFER_GONE" if rv.result == revalidate.OFFER_GONE \
                else "REVALIDATION_UNAVAILABLE"
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"],
                target=BookingOrderStatus.FAILED.value,
                cause=rv.result, actor=user_id,
                event_type="booking_blocked_revalidation",
                failure_code=code, failure_class="business",
                detail={"detail": rv.detail})
            result = OFFER_GONE if rv.result == revalidate.OFFER_GONE \
                else REVALIDATION_UNAVAILABLE
            return ExecutionOutcome(result, order=order, detail=rv.detail)

        # ── 状态 CAS：CONFIRMED → SUBMITTING（G20；重入时已是 SUBMITTING）──
        if not reentry:
            try:
                order = self.store.transition_order(
                    order_id=order["order_id"],
                    expected_status=BookingOrderStatus.CONFIRMED.value,
                    target=BookingOrderStatus.SUBMITTING.value,
                    cause="execute", actor=user_id,
                    event_type="booking_submit_started")
            except StaleTransition:
                current = self.store.get_order(order_id) or {}
                return ExecutionOutcome(IN_PROGRESS, order=current,
                                        detail="提交已在进行（并发收敛）")

        # ── 幂等外部副作用（G2：复用全局 PG 账本）────────
        from backend.shared.provider_idempotency import ensure_execution_allowed

        try:
            contract = ensure_execution_allowed(self.provider.name)
        except ProviderContractMissing as e:
            # 能力未登记/UNKNOWN：fail-closed（G17/G29 前置闸）
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=BookingOrderStatus.SUBMITTING.value,
                target=BookingOrderStatus.FAILED.value,
                cause="provider_contract_missing", actor=user_id,
                event_type="booking_failed",
                failure_code="PROVIDER_CONTRACT_MISSING",
                failure_class="rejected")
            return ExecutionOutcome(FAILED, order=order, detail=str(e))
        payload = _create_payload(order, quote)
        provider_key = derive_provider_key(
            tenant_id=order["tenant_id"], actor_id=order["user_id"],
            operation=OPERATION, client_key=order["merchant_order_id"],
            provider_name=self.provider.name, provider_operation="booking.create")
        last_outcome: list[ProviderEffectOutcome] = []

        def _call() -> dict:
            from backend.providers.travel.booking.contracts import (
                BookingCreateRequest,
            )

            request = BookingCreateRequest(
                merchant_order_id=order["merchant_order_id"],
                idempotency_key=provider_key,
                provider=self.provider.name,
                offer_fingerprint=quote["offer_fingerprint"],
                commerce_type=order["commerce_type"],
                booking_facts=quote["booking_facts"],
                amount=str(order["amount"]),
                currency=order["currency"],
                idempotency_key_transport=(
                    contract.key_transport.value
                    if contract.native_support.value == "supported" else "none"),
            )
            call_result = self.provider.create_booking(request)
            last_outcome.append(call_result.outcome)
            decision = decide_outcome_action(call_result.outcome, contract)
            raise_for_decision(decision)  # in_doubt→SideEffectOutcomeUnknown
            record = call_result.record
            return {
                "provider_order_id": record.provider_order_id if record else "",
                "provider": self.provider.name,
                "status": record.status if record else "",
                "amount": record.amount if record else "",
                "currency": record.currency if record else "",
            }

        try:
            # 与 run_idempotent_side_effect 同一装配（PG 权威账本 +
            # owner/接管语义）；直接组装以注入 ledger 表与连接工厂
            # （测试/评测隔离）——协议、状态、语义全部来自冻结层。
            from backend.shared.idempotency import (
                IdempotencyContextMissing,
                IdempotencyExecutor,
                IdempotencyKey,
                PostgresIdempotencyLedgerStore,
            )

            if not order["tenant_id"] or not order["user_id"]:
                raise IdempotencyContextMissing(
                    "缺少可信租户或操作者上下文，拒绝执行副作用")
            ledger = PostgresIdempotencyLedgerStore(
                self._conn_factory, table=self._ledger_table,
                owner_execution_id=owner_execution_id,
                takeover_allowed=lambda _info: takeover_allowed_for(contract),
            )
            key = IdempotencyKey(
                tenant_id=order["tenant_id"], actor_id=order["user_id"],
                operation=OPERATION, client_key=order["merchant_order_id"])
            result = IdempotencyExecutor(ledger).execute(key, payload, _call)
        except (IdempotencyUnavailable, SideEffectOutcomeUnknown) as e:
            # UNCERTAIN（UNKNOWN 结局/接管被拒/终态回写失败/Model C 冻结矩阵
            # in_doubt）：保守 IN_DOUBT，绝不猜成败
            order = self._transition_tolerant(
                order, BookingOrderStatus.IN_DOUBT, cause="provider_unknown",
                event_type="booking_in_doubt",
                failure_code="SIDE_EFFECT_UNKNOWN", failure_class="unknown")
            from backend.travel.booking.telemetry import record_in_doubt

            record_in_doubt(self.provider.name,
                            "matrix_in_doubt" if isinstance(
                                e, SideEffectOutcomeUnknown) else "uncertain")
            return ExecutionOutcome(IN_DOUBT, order=order, detail=str(e))
        except ProviderEffectError as e:
            outcome = last_outcome[-1] if last_outcome else None
            if outcome == ProviderEffectOutcome.UNKNOWN:
                # A/B 模型：账本已按冻结矩阵 FAILED（重试需先对账/同 key），
                # 订单状态保守 IN_DOUBT——绝不把未知说成明确失败（§二十四）
                from backend.travel.booking.telemetry import record_in_doubt

                record_in_doubt(self.provider.name, "unknown_reconcile_first")
                order = self._transition_tolerant(
                    order, BookingOrderStatus.IN_DOUBT,
                    cause="provider_unknown", event_type="booking_in_doubt",
                    failure_code="SIDE_EFFECT_UNKNOWN", failure_class="unknown")
                return ExecutionOutcome(IN_DOUBT, order=order, detail=str(e))
            code = "NOT_SENT" if outcome == ProviderEffectOutcome.NOT_SENT \
                else "REJECTED"
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=BookingOrderStatus.SUBMITTING.value,
                target=BookingOrderStatus.FAILED.value,
                cause=code.lower(), actor=user_id,
                event_type="booking_failed",
                failure_code=code,
                failure_class="not_sent" if code == "NOT_SENT" else "rejected",
                detail={"detail": str(e)})
            return ExecutionOutcome(FAILED, order=order, detail=str(e))

        # ── 收口 BOOKED（同事务事件；W5 webhook 已抢先则幂等收口）──
        try:
            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=BookingOrderStatus.SUBMITTING.value,
                target=BookingOrderStatus.BOOKED.value,
                cause="provider_confirmed", actor=user_id,
                event_type="booking_succeeded",
                provider_order_id=result.get("provider_order_id") or None)
        except StaleTransition:
            order = self.store.get_order(order_id) or order
        return ExecutionOutcome(BOOKED, order=order,
                                detail=f"provider_order={result.get('provider_order_id')}")

    # ────────────────────────────────────────────────
    def _transition_tolerant(self, order: dict, target: BookingOrderStatus,
                             *, cause: str, event_type: str,
                             failure_code: str | None = None,
                             failure_class: str | None = None) -> dict:
        """转换失败不掩盖主结局（并发被 webhook/recovery 抢先 = 收敛正常）。"""
        try:
            return self.store.transition_order(
                order_id=order["order_id"],
                expected_status=order["status"], target=target.value,
                cause=cause, event_type=event_type,
                failure_code=failure_code, failure_class=failure_class)
        except StaleTransition:
            return self.store.get_order(order["order_id"]) or order


def _quote_expired(quote: dict) -> bool:
    from datetime import datetime, timezone

    exp = quote.get("internal_expires_at")
    if not exp:
        return False
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= exp


def _supersede_and_requote(store: BookingStore, quote: dict,
                           rv) -> dict | None:
    """价格变化：旧 Quote 失效（superseded），由新价生成替代 Quote。

    新 Quote 独立生成（quote_id/fingerprint 重算）；是否继续由用户重新
    确认——绝不沿用旧确认（G9）。复核 offer 对象在 revalidate 内未保留
    完整模型（只有价格），替代 Quote 由调用方重新搜索命中后创建——这里
    仅标记旧 Quote，新 Quote 创建由 service 层完成。
    """
    store.mark_quote_status(quote["quote_id"], "superseded")
    return None


def _create_payload(order: dict, quote: dict) -> dict:
    from backend.travel.booking.identity import create_request_payload

    return create_request_payload(
        merchant_order_id=order["merchant_order_id"],
        provider=order["provider"],
        offer_fingerprint=quote["offer_fingerprint"],
        commerce_type=order["commerce_type"],
        booking_facts=quote["booking_facts"],
        amount=str(order["amount"]),
        currency=order["currency"],
    )
