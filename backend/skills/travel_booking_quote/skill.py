"""skills/travel_booking_quote/skill.py — 预订报价 Skill（收编重构 U4）

Capability: travel.booking_quote

无状态包装（create_quote 落 Quote+待确认订单）：报价 ≠ 预订成功；执行必须
经 travel_v2 REST 确认门（显式确认 → revalidate → 幂等执行）。事务机制
（executor/store/identity/revalidate/webhook/reconciliation/recovery）在
backend/travel/booking/ 原样保留，本 Skill 只是把「报价」暴露给主图编排面。
"""
from backend.skills.base import BaseSkill
from backend.tools.travel.booking import booking_quote_tool


class BookingQuoteSkill(BaseSkill):
    name = "travel_booking_quote"
    output_type = "structured"

    @property
    def _tool_fn(self):
        return booking_quote_tool
