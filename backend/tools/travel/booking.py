"""tools/travel/booking.py — 预订报价 Tool（收编重构 U4，Booking Executor 无状态面）

travel.booking_quote Skill 的实现面：BookingService.create_quote 的确定性
包装——生成 Quote + 待确认订单（TTL/指纹/金额快照），**不执行预订**。
执行必须显式确认（travel_v2 REST 确认门 /travel/v2/trips/{id}/bookings/
{intent}/confirm），确认门语义（TTL/金额互检/CAS/幂等账本）在
backend/travel/booking/ 原样保留。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result, tool_success_result
from backend.tools.tool_registry import tool_registry


@tool
def booking_quote_tool(
    commerce_type: str,
    search_params: str,
    selection: str = "",
) -> str:
    """
    创建预订报价（不执行预订）：确定性重搜后按序号/指纹锁定 offer，落 Quote
    与待确认订单，返回确认卡（金额/币种/截止时间/幂等标识）的 JSON。
    commerce_type: "hotel" | "flight"
    search_params: 确定性搜索参数 JSON 字符串（hotel: city/check_in/check_out/
    adults/children/rooms；flight: origin/destination/departure_date）
    selection: 选位 JSON 字符串，{"index": n}（1 起，价格升序）或
    {"offer_fingerprint": "..."}；默认选第 1 顺位
    适用场景：用户明确「要订」后的报价环节。注意：报价只是锁定待确认快照，
    绝不代表预订成功——执行必须由用户显式确认（确认门端点）。
    """
    import json as _json

    from backend.travel.booking.service import (
        BookingProviderOff,
        BookingService,
        OfferNotSelectable,
    )

    try:
        params = _json.loads(search_params) if search_params else {}
        sel = _json.loads(selection) if selection else {"index": 1}
    except ValueError:
        return tool_error_result("search_params/selection 必须是合法 JSON")

    if commerce_type not in ("hotel", "flight"):
        return tool_error_result("commerce_type 只支持 hotel | flight")

    from backend.core.request_context import get_tool_tenant_id, get_tool_user_id
    from backend.travel.booking.store import BookingStore

    tenant_id = get_tool_tenant_id().strip()
    user_id = get_tool_user_id().strip()
    if not tenant_id or not user_id:
        return tool_error_result(
            "预订报价需要完整的用户与租户身份（服务端注入），拒绝匿名报价")

    try:
        outcome = BookingService(store=BookingStore()).create_quote(
            tenant_id=tenant_id, user_id=user_id,
            commerce_type=commerce_type,
            search_params=params, selection=sel,
        )
    except BookingProviderOff:
        return tool_error_result(
            "预订功能未开通（provider off），未创建任何报价")
    except OfferNotSelectable as e:
        return tool_error_result(f"报价不可选：{e}")
    except ValueError as e:
        return tool_error_result(f"报价参数未通过校验：{e}")
    except Exception as e:  # noqa: BLE001 — Tool 边界归一
        logger.warning("[BookingQuoteTool] 报价失败: %s", e)
        return tool_error_result(f"报价创建失败：{type(e).__name__}")

    quote = outcome.quote
    return tool_success_result({
        "status": "awaiting_confirmation",
        "booking_intent_id": outcome.order["booking_intent_id"],
        "order_id": outcome.order["order_id"],
        "quote_id": quote["quote_id"],
        "amount": quote["price_amount"],
        "currency": quote["currency"],
        "internal_expires_at": quote.get("internal_expires_at"),
        "confirm_hint": "请通过预订确认门显式确认后才会执行；本报价不是订单",
    })


tool_registry.register(booking_quote_tool, __file__)
