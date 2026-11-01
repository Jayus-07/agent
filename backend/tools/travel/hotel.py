"""tools/travel/hotel.py — 住宿库存/报价检索 Tool（收编重构 U4）

travel.hotel.search ToolSpec（spec.py）的实现面：包装收编件
hotel_search_service（off/fake/live 三态）。只查不订——预订动作走
travel_v2 REST 确认门（quote → confirm），本工具绝不产生订单。
"""
from __future__ import annotations

from datetime import date

from langchain_core.tools import tool

from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result, tool_success_result
from backend.tools.tool_registry import tool_registry


@tool
def hotel_search_tool(
    city: str,
    check_in: str = "",
    check_out: str = "",
    adults: int = 2,
    rooms: int = 1,
) -> str:
    """
    检索城市住宿库存与报价（off/fake/live 三态数据源），返回候选名称、
    总价/币种、可订状态、指纹（selection_id）的 JSON 列表。
    city: 城市名，如「厦门」「大阪」
    check_in / check_out: 入住/退房日期（ISO 格式 YYYY-MM-DD；不传=无法查询库存）
    adults: 入住成人数（默认 2）；rooms: 房间数（默认 1）
    适用场景：行程住宿建议与档位比价。注意：报价是查询时点快照、随时可能
    变化；实际预订须走预订确认门（本工具只查不订）。
    """
    from backend.travel_v2.services.hotel_search_service import (
        HotelSearchParamError,
        search_hotel_offers,
    )

    city = (city or "").strip()
    if not city:
        return tool_error_result("住宿检索需要城市名", missing_fields=["city"])
    try:
        ci = date.fromisoformat(check_in) if check_in else None
        co = date.fromisoformat(check_out) if check_out else None
    except ValueError:
        return tool_error_result(
            "入住/退房日期格式无法确认（需 YYYY-MM-DD）",
            missing_fields=["check_in", "check_out"],
        )
    if ci is None or co is None:
        return tool_error_result(
            "库存查询需要入住与退房日期", missing_fields=["check_in", "check_out"])

    try:
        out = search_hotel_offers(
            city=city, check_in=ci, check_out=co,
            adults=max(1, int(adults or 2)), rooms=max(1, int(rooms or 1)),
        )
    except HotelSearchParamError as e:
        return tool_error_result(f"住宿检索参数未通过校验：{e}")
    except Exception as e:  # noqa: BLE001 — Tool 边界归一为结构化失败
        logger.warning("[HotelSearchTool] 检索失败: %s", e)
        return tool_error_result(f"住宿检索失败：{type(e).__name__}")

    if out["status"] in ("disabled", "timeout", "unavailable", "rate_limited",
                         "invalid_response", "unauthorized"):
        return tool_error_result(
            out.get("disclosure") or "住宿数据源暂时不可用，未返回未经核实的库存",
            source=out.get("source") or "-", query_status=out["status"],
        )
    return tool_success_result(
        {"city": city, "count": len(out["offers"]), "status": out["status"],
         "offers": out["offers"], "disclosure": out["disclosure"]},
    )


tool_registry.register(hotel_search_tool, __file__)
