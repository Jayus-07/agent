"""travel/commerce/reporter.py — Commerce 结果渲染（STOP K6，任务书 §十三/§十八）

渲染原则（与 travel reporter 同立场：**缺口要说出来**）：
  - Search Result ≠ Booking Guarantee：只说「搜索到的快照」，绝不出现
    已预订/已锁价/保证有房/保证有票/最终成交价（禁词清单 + 守卫测试）
  - stale 必须标注「非实时」；unknown 税费必须写「未知」；availability
    unknown 必须写「供应商未提供」，绝不写「有房」
  - Deep Link 文案只有「前往供应商查看」语义（§十三白名单）
  - Provider 失败如实告知「暂时无法获得实时库存」，绝不编造

链接文案白名单（§十三）：「前往供应商查看实时价格」；禁「立即预订/确认购买」。
"""
from __future__ import annotations

from backend.providers.travel.live.result import Freshness
from backend.travel.commerce.models import (
    Availability,
    FlightOffer,
    HotelOffer,
)
from backend.travel.commerce.service import CommerceResult

# 渲染禁词（守卫测试扫描本清单——出现任何一个即视为虚假验收话术）
FORBIDDEN_PHRASES = (
    "已预订", "已锁定", "已为你锁定", "预订成功", "确认购买", "立即预订",
    "保证有房", "保证有票", "已锁价", "锁价", "最终成交价", "最值得",
    "性价比最高", "最佳选择", "最推荐",
)

_AVAILABILITY_LABELS = {
    Availability.AVAILABLE: "可预订（供应商返回时点状态）",
    Availability.LIMITED: "库存紧张（供应商标注余量有限）",
    Availability.SOLD_OUT: "已订满（供应商返回时点状态）",
    Availability.UNKNOWN: "供应商未提供库存信息",
}

_FAILURE_TEXTS = {
    "timeout": "酒店/机票查询服务暂时超时，暂时无法获得实时库存。",
    "unavailable": "库存数据服务暂时不可用，暂时无法获得实时库存。",
    "rate_limited": "查询过于频繁或供应商额度受限，请稍后再试。",
    "invalid_response": "供应商返回了无法核实的数据，为避免误导已拒绝展示。",
    "unauthorized": "查询服务鉴权异常，暂时无法获得实时库存。",
    "disabled": None,  # disabled 有专属话术（区分 off 与 live 未接入）
    "not_found": "",   # not_found/empty 有专属话术
    "empty": "",
}

_FRESHNESS_TAG = {
    Freshness.CACHED: "（缓存数据）",
    Freshness.STALE: "（过期缓存，非实时）",
}


def render(result: CommerceResult) -> str:
    """CommerceResult → 用户可见 Markdown（纯函数，确定性）。"""
    if result.commerce_type == "hotel":
        return _render_hotel(result)
    return _render_flight(result)


# =============================================
# Hotel
# =============================================


def _render_hotel(result: CommerceResult) -> str:
    if result.status == "success":
        assert result.offers and isinstance(result.offers[0], HotelOffer)
        return _hotel_success(result)
    if result.status in ("empty", "not_found"):
        return (
            "没有找到符合条件的酒店。可以尝试更换城市或调整入住日期；"
            "当前未覆盖的目的地建议稍后再试。"
        )
    if result.status == "disabled":
        return f"酒店实时查询暂未开通：{result.reason}。"
    text = _FAILURE_TEXTS.get(result.status)
    if text is None:
        text = "暂时无法获得实时酒店库存。"
    return (
        f"{text}\n\n"
        "已如实停止查询——不会在服务异常时编造酒店或价格。"
        "可稍后重试，或出行前通过供应商官网确认。"
    )


def _hotel_success(result: CommerceResult) -> str:
    first: HotelOffer = result.offers[0]
    ci, co = first.check_in, first.check_out
    nights = first.nights
    occ = first.occupancy
    head = (
        f"### 酒店搜索结果（{first.city}，{ci} 入住 {co} 退房，共 {nights} 晚，"
        f"{occ.adults} 成人"
        + (f"/{occ.children} 儿童" if occ.children else "")
        + f"/{occ.rooms} 间房）\n\n"
        f"按**价格从低到高**排序（确定性排序，无推荐权重）。\n\n"
    )

    lines: list[str] = []
    for i, offer in enumerate(result.offers, start=1):
        snap = offer.price_snapshot
        tag = _FRESHNESS_TAG.get(offer.freshness, "")
        line = (
            f"**{i}. {offer.property_name}**{tag}\n"
            f"- 房型：{offer.room_type or '供应商未提供'}｜"
            f"可订状态：{_AVAILABILITY_LABELS[offer.availability.status]}\n"
            f"- 价格：{snap.amount.render()}"
        )
        if snap.tax_inclusion.value == "included":
            line += "（含税）"
        elif snap.tax_inclusion.value == "excluded":
            line += "（未含税）"
        if snap.taxes is not None:
            line += f"，税费 {snap.taxes.render()}"
        else:
            line += "，税费：**未知**（以供应商页面为准）"
        if snap.fees is not None:
            line += f"，费用 {snap.fees.render()}"
        line += (
            f"\n- 价格观测时间：{snap.observed_at}｜来源：{offer.provider}"
        )
        if snap.expires_at:
            line += f"｜供应商标注有效期至：{snap.expires_at}"
        if offer.cancellation_policy:
            line += f"\n- 取消政策：{offer.cancellation_policy}"
        else:
            line += "\n- 取消政策：供应商未提供（**未知**，预订前请确认）"
        if offer.meal_plan:
            line += f"｜餐食：{offer.meal_plan}"
        if offer.booking_deep_link:
            line += (
                f"\n- [前往供应商查看实时价格]({offer.booking_deep_link})"
                "（链接经安全校验；页面信息以供应商为准）"
            )
        lines.append(line)

    tail = _tail_disclosure(result)
    return head + "\n".join(lines) + ("\n\n" + tail if tail else "")


# =============================================
# Flight
# =============================================


def _render_flight(result: CommerceResult) -> str:
    if result.status == "success":
        assert result.offers and isinstance(result.offers[0], FlightOffer)
        return _flight_success(result)
    if result.status in ("empty", "not_found"):
        return (
            "没有找到符合条件的航班。可以尝试更换出发地/目的地或日期；"
            "当前未覆盖的航线建议稍后再试。"
        )
    if result.status == "disabled":
        return f"机票实时查询暂未开通：{result.reason}。"
    text = _FAILURE_TEXTS.get(result.status)
    if text is None:
        text = "暂时无法获得实时航班库存。"
    return (
        f"{text}\n\n"
        "已如实停止查询——不会在服务异常时编造航班或票价。"
        "可稍后重试，或出行前通过航司官方渠道确认。"
    )


def _flight_success(result: CommerceResult) -> str:
    first: FlightOffer = result.offers[0]
    head = (
        f"### 航班搜索结果（{first.origin} → {first.destination}，"
        f"{first.segments[0].departure_at[:10]}，按**价格从低到高**排序）\n\n"
    )

    lines: list[str] = []
    for i, offer in enumerate(result.offers, start=1):
        snap = offer.price_snapshot
        tag = _FRESHNESS_TAG.get(offer.freshness, "")
        seg_desc = " → ".join(
            f"{s.carrier}{s.flight_number}（{s.origin_airport}"
            f"{s.departure_at[11:16]}→{s.destination_airport}"
            f"{s.arrival_at[11:16]}）" for s in offer.segments)
        stop_note = (
            f"中转 {offer.stops} 次" if offer.stops > 0 else "直飞")
        duration = (
            f"｜全程约 {offer.duration_minutes} 分钟"
            if offer.duration_minutes is not None else "")
        line = (
            f"**{i}. {seg_desc}**{tag}\n"
            f"- {stop_note}{duration}"
            + (f"｜舱位：{offer.cabin}" if offer.cabin else "")
            + f"｜可订状态：{_AVAILABILITY_LABELS[offer.availability.status]}\n"
            f"- 价格：{snap.amount.render()}"
        )
        if snap.tax_inclusion.value == "included":
            line += "（含税）"
        elif snap.tax_inclusion.value == "excluded":
            line += "（未含税）"
        if snap.taxes is not None:
            line += f"，税费 {snap.taxes.render()}"
        else:
            line += "，税费：**未知**（以供应商页面为准）"
        line += (
            f"\n- 价格观测时间：{snap.observed_at}｜来源：{offer.provider}"
        )
        if offer.baggage:
            line += f"｜行李：{offer.baggage}"
        if offer.fare_rules:
            line += f"\n- 退改规则：{offer.fare_rules}"
        else:
            line += "\n- 退改规则：供应商未提供（**未知**，预订前请确认）"
        if offer.booking_deep_link:
            line += (
                f"\n- [前往供应商查看实时价格]({offer.booking_deep_link})"
                "（链接经安全校验；页面信息以供应商为准）"
            )
        lines.append(line)

    tail = _tail_disclosure(result)
    return head + ("\n\n".join(lines)) + ("\n\n" + tail if tail else "")


# =============================================
# 公共披露
# =============================================


def _tail_disclosure(result: CommerceResult) -> str:
    notes: list[str] = [
        "以上为**搜索时点的价格快照**，不构成预订保证；"
        "可订状态与价格以供应商页面实时信息为准。"
    ]
    if result.freshness == Freshness.STALE:
        notes.insert(0, "⚠ 供应商查询失败，以下展示的是**过期缓存数据，非实时"
                        "报价**，仅供价格水平参考。")
    elif result.freshness == Freshness.CACHED:
        notes.insert(0, "（本次结果来自缓存，非实时查询。）")
    if result.truncated:
        notes.append(f"结果较多，仅展示价格最低的前 {_max_offers()} 条"
                     f"（其余 {result.truncated} 条已省略）。")
    if result.rejects:
        notes.append(f"另有 {len(result.rejects)} 条供应商数据未通过校验，"
                     "已拒绝展示（不提供无法核实的信息）。")
    from backend.config.travel_commerce import provider_mode

    if provider_mode() == "fake":
        notes.append("⚠ 当前数据源为**测试数据（fake:commerce）**，"
                     "仅用于链路验证，不是真实报价。")
    return "\n\n".join(notes)


def _max_offers() -> int:
    from backend.config.travel_commerce import TRAVEL_COMMERCE_MAX_OFFERS

    return max(1, TRAVEL_COMMERCE_MAX_OFFERS)
