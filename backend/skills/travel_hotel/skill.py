"""skills/travel_hotel/skill.py — 住宿检索 Skill（收编重构 U4）

Capability: travel.hotel_search

无状态单点能力（收编件 hotel_search_service 三态数据源），V2 工作台与主图
共用；只查不订——预订走 travel.booking_quote（报价）与 REST 确认门（执行）。
"""
from backend.skills.base import BaseSkill
from backend.tools.travel.hotel import hotel_search_tool


class HotelSearchSkill(BaseSkill):
    name = "travel_hotel"
    output_type = "structured"

    @property
    def _tool_fn(self):
        return hotel_search_tool
