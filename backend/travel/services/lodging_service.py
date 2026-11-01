"""travel/services/lodging_service.py — 住宿档位与库存服务（收编重构 U2 新增）

职责（AGENTS-DESIGN A5）：按行程日期与目的地检索住宿候选（档位匹配 +
实时库存报价），产出 nightly_price / check_in / check_out 候选
（booking_status=not_booked），喂给 Budget Expert 的住宿成本项；
「预订」动作不在此层——走 travel_v2 REST 确认门。

数据源：收编件 hotel_search_service（off/fake/live 三态，单一事实源）。
live 供应商未接入 → disabled 如实披露；fake → 强制「测试数据」披露。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from backend.travel_v2.services.hotel_search_service import (
    HotelSearchParamError,
    search_hotel_offers,
)

# 档位 → 价格区间（CNY/晚；用于「预算档位匹配」的确定性初筛——真实库存
# 缺失时的档位语义锚点；候选单价落在区间内才计入该档位）
_TIER_BANDS_CNY: dict[str, tuple[int, int]] = {
    "low": (0, 300),
    "mid": (300, 700),
    "high": (700, 10_000),
}


def nightly_price_cny(price_total: str, currency: str, nights: int) -> float | None:
    """总额 → 单晚价（确定性除法；非 CNY 币种返回 None——不做隐式换算，
    与 H7/F8「币种保真」纪律一致，档位匹配只对 CNY 生效）。"""
    if currency != "CNY":
        return None
    try:
        total = float(price_total)
    except (TypeError, ValueError):
        return None
    if nights <= 0:
        return None
    return round(total / nights, 2)


def tier_for_price(nightly: float | None) -> str | None:
    """单晚价 → 档位（区间外/未知返回 None，不硬归档）。"""
    if nightly is None:
        return None
    for tier, (low, high) in _TIER_BANDS_CNY.items():
        if low <= nightly < high:
            return tier
    return None


def search_lodging(
    *, destination: str, check_in: date | None, check_out: date | None,
    budget_tier: str | None = None, adults: int = 2, rooms: int = 1,
) -> dict[str, Any]:
    """住宿候选检索（三态语义直通收编件）。

    Returns:
        dict：status / source / disclosure / options[{name, selection_id,
        nightly_price_cny, tier, check_in, check_out, availability, deep_link}]。
    Raises:
        HotelSearchParamError：日期非法（调用方转澄清话术）。
    """
    if check_in is None or check_out is None or check_out <= check_in:
        return {
            "status": "skipped",
            "source": "-",
            "disclosure": "行程未提供有效入住/退房日期，未查询住宿",
            "options": [],
        }
    nights = (check_out - check_in).days
    out = search_hotel_offers(
        city=destination, check_in=check_in, check_out=check_out,
        adults=adults, rooms=rooms,
    )
    options: list[dict[str, Any]] = []
    for offer in out.get("offers") or []:
        nightly = nightly_price_cny(
            offer["price_total"], offer["currency"], nights)
        tier = tier_for_price(nightly)
        if budget_tier and tier is not None and tier != budget_tier:
            continue
        options.append({
            "selection_id": offer["selection_id"],
            "name": offer["property_name"],
            "city": offer["city"],
            "address": offer["address"],
            "lat": offer["lat"],
            "lng": offer["lng"],
            "check_in": offer["check_in"],
            "check_out": offer["check_out"],
            "nights": nights,
            "price_total": offer["price_total"],
            "currency": offer["currency"],
            "nightly_price_cny": nightly,
            "tier": tier,
            "availability": offer["availability"],
            "rating": None,
            "booking_status": "not_booked",
            "deep_link": offer["booking_deep_link"],
        })
    options.sort(
        key=lambda o: (o["nightly_price_cny"] is None,
                       o["nightly_price_cny"] or 0.0))
    return {
        "status": out["status"],
        "source": out["source"],
        "freshness": out.get("freshness"),
        "disclosure": out["disclosure"],
        "options": options,
    }


def lodging_cost_note(result: dict[str, Any]) -> str | None:
    """预算面摘要：给出可用于费用核算的口径说明（如实，不猜）。"""
    if result.get("status") != "success" or not result.get("options"):
        return "住宿费用暂无法估算（实时库存不可用），预算未含住宿项"
    first = result["options"][0]
    if first.get("nightly_price_cny") is None:
        return "住宿报价为非 CNY 币种，预算未做隐式换算，未含住宿项"
    return None
