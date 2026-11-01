"""travel_v2/services/hotel_search_service.py — 酒店数据源搜索（收编件 V2 形态）

包装 providers_support/commerce 的 normalize/ranking/factory（off/fake/live
三态），供 `travel.hotel.search` ToolSpec 与后续 /travel/v2/search/hotels
数据源切换使用。

语义沿收编件（原 STOP K §9 结局矩阵）：
  - provider 缺失 = disabled（不伪造）；全部记录被拒 = invalid_response
    （不是「无结果」）；SUCCESS+[] 才是 empty；
  - 失败结局不产生任何 offer（结构性不可能映射为 sold_out）；
  - 金额 Decimal 全链，JSON 落字符串。

注：现有 `/travel/v2/search/hotels` 端点仍走高德商户检索（无房态/房价，
如实披露）——在 rollinggo/真实供应商就绪、TRAVEL_COMMERCE_PROVIDER_MODE
可配 live 之前，切换该端点会把酒店 Tab 打回「未启用」，属用户可见回归；
切换时点随 U 系列数据源接线另行落地。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from backend.travel.providers_support.commerce import offer_search
from backend.travel.providers_support.commerce.request import (
    HotelSearchRequest,
    horizon_limit,
)

# 可如实呈现给用户的失败族（与 offer_search._PRESENTABLE_FAILURE 同口径）
_PRESENTABLE = {
    "not_found", "timeout", "unavailable", "rate_limited",
    "invalid_response", "unauthorized", "disabled",
}

_FAKE_DISCLOSURE = "测试数据：当前为 fake 数据源，仅供链路验收，严禁作为生产依据。"
_DISABLED_DISCLOSURE = "实时酒店库存查询未启用；未展示任何未经核实的库存或价格。"
_LIVE_DISCLOSURE = "库存与价格为查询时点快照，随时可能变化；下单前以确认门复核结果为准。"


class HotelSearchParamError(ValueError):
    """搜索参数不合法（确定性校验拒绝，可读信息直达澄清话术）。"""


def search_hotel_offers(
    *, city: str, check_in: date, check_out: date,
    adults: int = 2, children: int = 0, rooms: int = 1,
    star_rating: int | None = None, hotel_name: str | None = None,
) -> dict[str, Any]:
    """酒店库存/报价搜索（收编件唯一入口；参数绑定由请求模型保证）。

    Returns:
        dict：status / source / queried 面向 V2 与 ToolSpec 消费；
        offers 内每条含 selection_id（= offer_fingerprint，预订 Quote 定位键）。
    Raises:
        HotelSearchParamError：参数未过确定性校验（日期/人数/晚数）。
    """
    try:
        req = HotelSearchRequest(
            city=city, check_in=check_in, check_out=check_out,
            adults=adults, children=children, rooms=rooms,
            star_rating=star_rating, hotel_name=hotel_name,
        )
    except ValidationError as e:
        raise HotelSearchParamError(_readable(e)) from e

    result = offer_search.search_hotels(req)
    # 结局白名单：success/empty + 可呈现失败族；未知结局一律归 unavailable
    _KNOWN = {"success", "empty", *_PRESENTABLE}
    status = result.status if result.status in _KNOWN else "unavailable"
    return {
        "status": status,
        "source": result.provider or "-",
        "freshness": result.freshness.value,
        "reason": result.reason,
        "truncated": result.truncated,
        "offers": [_offer_payload(o) for o in result.offers],
        "disclosure": _disclosure_for(status),
    }


def _offer_payload(offer) -> dict[str, Any]:
    """HotelOffer → V2 offer 字典（金额保持 Decimal 字符串语义）。"""
    snap = offer.price_snapshot
    return {
        "selection_id": offer.offer_fingerprint,
        "provider": offer.provider,
        "property_id": offer.property_id,
        "property_name": offer.property_name,
        "city": offer.city,
        "address": offer.address,
        "lat": offer.lat,
        "lng": offer.lng,
        "check_in": offer.check_in,
        "check_out": offer.check_out,
        "nights": offer.nights,
        "room_type": offer.room_type,
        "occupancy": offer.occupancy.model_dump(),
        "availability": offer.availability.status.value,
        "availability_observed_at": offer.availability.observed_at,
        "price_total": str(snap.amount.amount),
        "currency": snap.amount.currency,
        "tax_inclusion": snap.tax_inclusion.value,
        "taxes": str(snap.taxes.amount) if snap.taxes is not None else None,
        "fees": str(snap.fees.amount) if snap.fees is not None else None,
        "price_observed_at": snap.observed_at,
        "price_expires_at": snap.expires_at,
        "freshness": snap.freshness.value,
        "booking_deep_link": offer.booking_deep_link,
        "cancellation_policy": offer.cancellation_policy,
        "meal_plan": offer.meal_plan,
    }


def _disclosure_for(status: str) -> str:
    from backend.config.travel_offer_search import provider_mode

    if status == "disabled":
        return _DISABLED_DISCLOSURE
    if provider_mode() == "fake":
        return _FAKE_DISCLOSURE
    return _LIVE_DISCLOSURE


def _readable(err: ValidationError) -> str:
    """Pydantic 校验错误 → 首条可读信息（澄清话术直达）。"""
    first = err.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    msg = first.get("msg", "参数不合法")
    return f"{'/'.join(filter(None, (loc,)))}: {msg}" if loc else msg


def searchable_horizon() -> date:
    """可查询的最远日期（供澄清话术引用；配置口径单点）。"""
    return horizon_limit()
