"""travel/booking/revalidate.py — 确认后价格/库存复核（STOP L2，G9/G10，P0 Gate）

用户确认的价格 ≠ 执行时供应商价格。Booking Create 之前必须复核：
  价格变化（Decimal 精确比较，禁 float）→ 停止执行、旧 Quote 失效、
             生成新 Quote、要求重新确认（禁止静默多扣）；
  库存售罄/offer 消失 → 停止执行（create=0）；
  无法复核（provider 不可用）→ 禁止假定仍 AVAILABLE → 停止执行并如实披露。

复核 = 复用 STOP K commerce service 的**确定性重搜**（同参数同结果，
缓存吸收，G2 复用面），按 offer_fingerprint 精确匹配原 offer。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.travel.commerce import service as commerce_service
from backend.travel.commerce.request import (
    FlightSearchRequest,
    HotelSearchRequest,
)

# 复核结局（§十二/§十三语义，create 是否放行的唯一依据）
OK = "ok"
PRICE_CHANGED = "price_changed"
OFFER_GONE = "offer_gone"            # 结果空/未匹配（供给消失，create=0）
SOLD_OUT = "sold_out"                # Provider 明示售罄（create=0）
UNAVAILABLE = "unavailable"          # 无法复核——禁止假定 AVAILABLE


@dataclass
class RevalidationOutcome:
    result: str
    new_price: str | None = None       # price_changed 时的最新价（Decimal 字符串）
    new_currency: str | None = None
    new_snapshot_offer: dict | None = None  # price_changed 时用于生成替代 Quote
    detail: str = ""


def _request_from_facts(commerce_type: str, facts: dict):
    if commerce_type == "hotel":
        return HotelSearchRequest(
            city=facts["city"], check_in=_date(facts["check_in"]),
            check_out=_date(facts["check_out"]), adults=facts.get("adults", 2),
            children=facts.get("children", 0), rooms=facts.get("rooms", 1))
    return FlightSearchRequest(
        origin=facts["origin"], destination=facts["destination"],
        departure_date=_date(facts["departure_date"]),
        adults=facts.get("adults", 1), children=facts.get("children", 0))


def _date(value: str):
    from datetime import date as _d

    return _d.fromisoformat(value)


def revalidate_quote(quote: dict) -> RevalidationOutcome:
    """按 Quote 存的原始搜索参数重搜，按 offer_fingerprint 精确匹配复核。"""
    commerce_type = quote["commerce_type"]
    facts = quote["booking_facts"]
    if commerce_type == "hotel":
        req = _request_from_facts("hotel", facts)
        result = commerce_service.search_hotels(req)
    else:
        req = _request_from_facts("flight", facts)
        result = commerce_service.search_flights(req)

    if result.status == "disabled":
        return RevalidationOutcome(UNAVAILABLE, detail="commerce provider disabled")
    if result.status in ("timeout", "unavailable", "rate_limited",
                         "invalid_response", "unauthorized"):
        # 无法复核：禁止假定仍 AVAILABLE（§十三），停止执行
        return RevalidationOutcome(UNAVAILABLE, detail=f"revalidation {result.status}")
    if result.status in ("empty", "not_found"):
        return RevalidationOutcome(OFFER_GONE, detail="搜索已无结果")

    matched = None
    for offer in result.offers:
        if offer.offer_fingerprint == quote["offer_fingerprint"]:
            matched = offer
            break
    if matched is None:
        # 原 offer（同物业同 rate 同条件）已不在结果中：供给消失，create=0
        return RevalidationOutcome(OFFER_GONE, detail="offer 指纹未再命中")

    if matched.availability.status.value == "sold_out":
        return RevalidationOutcome(SOLD_OUT, detail="offer sold out")

    new_amount = matched.price_snapshot.amount
    if Decimal(str(quote["price_amount"])) != new_amount.amount \
            or (quote["currency"] or "").upper() != new_amount.currency:
        return RevalidationOutcome(
            PRICE_CHANGED,
            new_price=str(new_amount.amount),
            new_currency=new_amount.currency,
            detail=f"价格 {quote['price_amount']} {quote['currency']} → "
                   f"{new_amount.amount} {new_amount.currency}")
    return RevalidationOutcome(OK)
