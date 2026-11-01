"""tools/travel/food.py — 餐饮商户检索 Tool（收编重构 U4，Dining Scout 无状态面）

高德商户检索（food capability）的无状态包装：候选、评分、营业状态、均价。
偏好过滤（不辣/清淡等）属 Dining Scout 编排（auxiliary_tasks + L3 记忆），
不在 Tool 层重复实现——Tool 只搬运事实。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result, tool_success_result
from backend.tools.tool_registry import tool_registry


@tool
def food_search_tool(city: str, limit: int = 10) -> str:
    """
    检索城市餐饮商户候选（高德），返回名称/评分/营业状态/人均/坐标的 JSON 列表。
    city: 城市名，如「福州」「厦门」
    limit: 返回条数上限（默认 10，最大 20）
    适用场景：为行程选晚餐位/午餐位拉取真实商户候选。注意返回的是候选池，
    不含位次安排与预订——预订类诉求不在本工具范围。
    """
    limit = max(1, min(int(limit or 10), 20))
    from backend.travel.services import live_search_service

    try:
        data = live_search_service.search_food((city or "").strip())
    except Exception as e:  # noqa: BLE001 — Tool 边界归一为结构化失败
        logger.warning("[FoodSearchTool] 检索失败: %s", e)
        return tool_error_result(f"美食检索失败：{type(e).__name__}")
    merchants = (data.get("merchants") or [])[:limit]
    status = data.get("status") or ""
    if status in ("disabled", "provider_unavailable", "network_timeout",
                  "business_failure"):
        return tool_error_result(
            "美食数据源暂时不可用，未返回未经核实的商户",
            source=data.get("source") or "amap", query_status=status,
        )
    return tool_success_result(
        {"city": city, "count": len(merchants),
         "merchants": merchants,
         "disclosure": "商户评分、营业状态与位置来自高德，可能随时间变化"},
    )


tool_registry.register(food_search_tool, __file__)
