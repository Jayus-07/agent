"""travel/booking/service.py — Booking 服务编排（STOP L1/L2/L8）

用户可见闭环（§七）：Offer 选择 → Quote 落库 → 显式确认 → 门+幂等执行 →
状态呈现。所有事实（金额/币种/provider）服务端重读；身份从调用方显式
传入（认证边界解析），绝不取自消息文本。

Offer 选择：**确定性重搜**（复用 commerce service；同参数同排序）后按
序号（1 起，排序即价格升序）或 offer_fingerprint 精确选中——无跨轮状态
依赖，天然复核。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.travel.booking.authorization import (
    assert_owns_order,
    assert_owns_quote,
)
from backend.travel.booking.executor import (
    BookingExecutor,
    ExecutionOutcome,
)
from backend.travel.booking.identity import (
    booking_intent_id,
    canonical_amount,
    confirmation_fingerprint,
    merchant_order_id,
    quote_fingerprint,
)
from backend.travel.booking.state import BookingOrderStatus
from backend.travel.booking.store import BookingStore


@dataclass
class QuoteOutcome:
    quote: dict
    order: dict            # awaiting_confirmation（确认 gate 已绑定）
    offer_summary: dict    # 渲染用选中 offer 摘要


class BookingService:
    def __init__(self, store: BookingStore | None = None,
                 provider_factory=None, quote_ttl_seconds: int | None = None,
                 ledger_table: str = "ai.idempotency_records",
                 conn_factory=None):
        self.store = store or BookingStore()
        self._provider_factory = provider_factory or _default_provider_factory
        self._quote_ttl = quote_ttl_seconds
        self._ledger_table = ledger_table
        self._conn_factory = conn_factory

    # ────────────────────────────────────────────────
    # 1) Offer → Quote（§八：不可变事实快照，非 Offer 指针）
    # ────────────────────────────────────────────────
    def create_quote(self, *, tenant_id: str, user_id: str,
                     commerce_type: str, search_params: dict,
                     selection: dict) -> QuoteOutcome:
        """search_params：确定性搜索参数；selection：{"index": n} 或
        {"offer_fingerprint": "..."}。"""
        offer, provider_name = _resolve_offer(
            commerce_type, search_params, selection)
        facts = dict(search_params)
        ttl = self._quote_ttl if self._quote_ttl is not None else _default_ttl()
        now = datetime.now(timezone.utc)
        amount_str = canonical_amount(offer.price_snapshot.amount.amount)
        currency = offer.price_snapshot.amount.currency
        qfp = quote_fingerprint(
            tenant_id=tenant_id, provider=provider_name,
            offer_fingerprint=offer.offer_fingerprint,
            booking_facts=facts,
            amount=amount_str, currency=currency)
        quote_id = "bq_" + uuid.uuid4().hex[:20]
        snap = offer.price_snapshot
        quote = self.store.create_quote({
            "quote_id": quote_id,
            "tenant_id": tenant_id, "user_id": user_id,
            "commerce_type": commerce_type,
            "provider": provider_name,
            "provider_offer_id": offer.provider_offer_id,
            "offer_fingerprint": offer.offer_fingerprint,
            "booking_facts": facts,
            "price_amount": amount_str,
            "currency": currency,
            "taxes": str(snap.taxes.amount) if snap.taxes is not None else None,
            "fees": str(snap.fees.amount) if snap.fees is not None else None,
            "tax_inclusion": snap.tax_inclusion.value,
            "availability": offer.availability.status.value,
            "provider_observed_at": snap.observed_at,
            "provider_expires_at": snap.expires_at,   # 仅 Provider 明示；None=未承诺
            "internal_expires_at": now + timedelta(seconds=ttl),
            "quote_fingerprint": qfp,
        })
        self.store.append_event(
            tenant_id=tenant_id, order_id=None, quote_id=quote_id,
            event_type="quote_created", actor=user_id, result="created",
            detail={"offer_fingerprint": offer.offer_fingerprint,
                    "amount": amount_str, "currency": currency})

        # 确认 gate 绑定快照（§十一：确认的是具体价格与具体订单事实）
        cfp = confirmation_fingerprint(
            quote_id=quote_id, quote_fingerprint=qfp,
            amount=amount_str, currency=currency,
            provider=_booking_provider_for(), operation="travel.booking.create",
            tenant_id=tenant_id, user_id=user_id)
        intent = booking_intent_id(
            tenant_id=tenant_id, user_id=user_id, quote_id=quote_id,
            confirmation_fingerprint=cfp)
        mo_id = merchant_order_id(tenant_id=tenant_id, intent_id=intent,
                                  provider=_booking_provider_for())
        order, _created = self.store.create_order({
            "order_id": "bo_" + uuid.uuid4().hex[:20],
            "tenant_id": tenant_id, "user_id": user_id,
            "booking_intent_id": intent,
            "quote_id": quote_id,
            "confirmation_id": cfp,
            "commerce_type": commerce_type,
            "provider": _booking_provider_for(),
            "merchant_order_id": mo_id,
            "idempotency_key": _ledger_key(tenant_id, user_id, mo_id),
            "amount": amount_str,
            "currency": currency,
        })
        summary = {
            "name": getattr(offer, "property_name", None)
            or getattr(offer, "origin", "") + "→" + getattr(offer, "destination", ""),
            "amount": amount_str,
            "currency": currency,
            "observed_at": snap.observed_at,
            "internal_expires_at": quote["internal_expires_at"],
            "provider_expires_at": quote["provider_expires_at"],
        }
        return QuoteOutcome(quote=quote, order=order, offer_summary=summary)

    # ────────────────────────────────────────────────
    # 2) 显式确认 → 门 + 幂等执行（§十一/§二十一）
    # ────────────────────────────────────────────────
    def confirm_and_execute(self, *, tenant_id: str, user_id: str,
                            order_id: str | None = None) -> ExecutionOutcome:
        if order_id:
            order = assert_owns_order(self.store.get_order(order_id),
                                      tenant_id=tenant_id, user_id=user_id)
        else:
            order = self.store.latest_order_for_user(
                tenant_id, user_id,
                [BookingOrderStatus.AWAITING_CONFIRMATION.value])
            if order is None:
                order = self.store.latest_order_for_user(
                    tenant_id, user_id,
                    [BookingOrderStatus.CONFIRMED.value,
                     BookingOrderStatus.SUBMITTING.value,
                     BookingOrderStatus.IN_DOUBT.value,
                     BookingOrderStatus.BOOKED.value,
                     BookingOrderStatus.FAILED.value,
                     BookingOrderStatus.EXPIRED.value])
            if order is None:
                return ExecutionOutcome(
                    "not_confirmable", order={}, detail="没有待确认的预订")
            assert_owns_order(order, tenant_id=tenant_id, user_id=user_id)

        quote = assert_owns_quote(self.store.get_quote(order["quote_id"]),
                                  tenant_id=tenant_id, user_id=user_id)

        if order["status"] == BookingOrderStatus.AWAITING_CONFIRMATION.value:
            # 确认绑定校验（G8）：当前 Quote 事实指纹必须与 gate 绑定一致
            cfp = confirmation_fingerprint(
                quote_id=quote["quote_id"],
                quote_fingerprint=quote["quote_fingerprint"],
                amount=canonical_amount(order["amount"]),
                currency=order["currency"],
                provider=order["provider"],
                operation="travel.booking.create",
                tenant_id=tenant_id, user_id=user_id)
            if cfp != order["confirmation_id"]:
                return ExecutionOutcome(
                    "not_confirmable", order=order,
                    detail="确认绑定不一致（Quote 事实已变化）")
            if _quote_expired(quote):
                order = self.store.transition_order(
                    order_id=order["order_id"],
                    expected_status=order["status"],
                    target=BookingOrderStatus.EXPIRED.value,
                    cause="confirmation_expired", actor=user_id,
                    event_type="booking_confirmation_expired",
                    failure_code="QUOTE_EXPIRED", failure_class="business")
                return ExecutionOutcome(
                    "confirmation_expired", order=order, detail="报价已过期")
            from backend.travel.booking.telemetry import record_transition

            order = self.store.transition_order(
                order_id=order["order_id"],
                expected_status=BookingOrderStatus.AWAITING_CONFIRMATION.value,
                target=BookingOrderStatus.CONFIRMED.value,
                cause="user_confirmed", actor=user_id,
                event_type="confirmation_confirmed",
                detail={"confirmation_id": order["confirmation_id"][:16]})
            record_transition("awaiting_confirmation", "confirmed")

        provider, contract = self._resolve_booking_provider()
        executor = BookingExecutor(
            self.store, provider, contract,
            ledger_table=self._ledger_table, conn_factory=self._conn_factory)
        outcome = executor.execute(order_id=order["order_id"],
                                   tenant_id=tenant_id, user_id=user_id)
        from backend.travel.booking.telemetry import record_request

        record_request(order["provider"], "travel.booking.create",
                       outcome.result)
        return outcome

    # ────────────────────────────────────────────────
    # 3) 状态查询（§三十七：真实状态；IN_DOUBT 如实）
    # ────────────────────────────────────────────────
    def status_report(self, *, tenant_id: str, user_id: str,
                      order_id: str | None = None) -> dict | None:
        if order_id:
            order = assert_owns_order(self.store.get_order(order_id),
                                      tenant_id=tenant_id, user_id=user_id)
        else:
            order = self.store.latest_order_for_user(
                tenant_id, user_id, ["booked", "failed", "in_doubt",
                                     "submitting", "awaiting_confirmation",
                                     "confirmed", "expired"])
            if order is None:
                return None
        return {
            "order": order,
            "events": self.store.events_for_order(order["order_id"]),
            "quote": self.store.get_quote(order["quote_id"]),
        }

    def _resolve_booking_provider(self):
        return self._provider_factory()


# ────────────────────────────────────────────────


def _booking_provider_for() -> str:
    from backend.config.travel_booking import provider_name

    return provider_name()


def _default_ttl() -> int:
    from backend.config.travel_booking import TRAVEL_BOOKING_QUOTE_TTL_SECONDS

    return max(60, TRAVEL_BOOKING_QUOTE_TTL_SECONDS)


def _default_provider_factory():
    from backend.travel.booking.provider_capabilities import resolve_provider

    from backend.config.travel_booking import provider_name

    resolved = resolve_provider(provider_name())
    if resolved is None:
        raise BookingProviderOff("TRAVEL_BOOKING_PROVIDER=off")
    return resolved


class BookingProviderOff(RuntimeError):
    """Booking provider 未启用——调用方如实告知，不伪造执行。"""


def _ledger_key(tenant_id: str, user_id: str, merchant_order_id: str) -> str:
    """幂等 ledger 的 client_key 维度展示值（与 derive_provider_key 输入
    一致的稳定业务标识；实际 provider key 由执行层派生）。"""
    from backend.shared.provider_idempotency import derive_provider_key

    return derive_provider_key(
        tenant_id=tenant_id, actor_id=user_id,
        operation="travel.booking.create", client_key=merchant_order_id,
        provider_name=_booking_provider_for(),
        provider_operation="booking.create")[:64]


def _resolve_offer(commerce_type: str, search_params: dict, selection: dict):
    """确定性重搜 + 精确选中（index 1 起按排序位；或 offer_fingerprint）。"""
    from backend.travel.commerce import service as commerce_service
    from backend.travel.commerce.request import (
        FlightSearchRequest,
        HotelSearchRequest,
    )
    from datetime import date as _d

    if commerce_type == "hotel":
        req = HotelSearchRequest(
            city=search_params["city"],
            check_in=_d.fromisoformat(search_params["check_in"]),
            check_out=_d.fromisoformat(search_params["check_out"]),
            adults=search_params.get("adults", 2),
            children=search_params.get("children", 0),
            rooms=search_params.get("rooms", 1))
        result = commerce_service.search_hotels(req)
    else:
        req = FlightSearchRequest(
            origin=search_params["origin"],
            destination=search_params["destination"],
            departure_date=_d.fromisoformat(search_params["departure_date"]),
            adults=search_params.get("adults", 1),
            children=search_params.get("children", 0))
        result = commerce_service.search_flights(req)

    if result.status != "success" or not result.offers:
        raise OfferNotSelectable(f" commerce 搜索不可选（{result.status}）")

    fp = selection.get("offer_fingerprint")
    if fp:
        for offer in result.offers:
            if offer.offer_fingerprint == fp:
                return offer, result.provider
        raise OfferNotSelectable("offer_fingerprint 未命中")
    index = int(selection.get("index", 1))
    if index < 1 or index > len(result.offers):
        raise OfferNotSelectable(f"序号 {index} 超出范围（1-{len(result.offers)}）")
    return result.offers[index - 1], result.provider


class OfferNotSelectable(ValueError):
    """Offer 不可选（搜索失败/序号越界/指纹未命中）——如实披露。"""


def _quote_expired(quote: dict) -> bool:
    exp = quote.get("internal_expires_at")
    if not exp:
        return False
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= exp
