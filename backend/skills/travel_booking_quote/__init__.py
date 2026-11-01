"""skills/travel_booking_quote — 预订报价 Skill 包

import 本包即完成两件事（与其他 Skill 包同一约定）：
  1. 向 orchestration.capability_registry 注册图节点 travel_booking_quote_skill
  2. 由 skills/registry.py 注册 Skill 实例（capability: travel.booking_quote）

报价 ≠ 预订成功；执行必须经 travel_v2 REST 确认门显式确认。
"""
from backend.orchestration.capability_registry import tool_registry
from backend.skills.travel_booking_quote.skill import BookingQuoteSkill


async def travel_booking_quote_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）"""
    from backend.shared.logger import logger

    skill = BookingQuoteSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get(
        "capability", "travel.booking_quote")
    logger.info("[TravelBookingQuoteSkill] step=%s cap=%s",
                state.get("current_step_id"), cap)
    return await skill.execute(state, step_capability=cap)


tool_registry.register_skill_node(
    "travel_booking_quote_skill", travel_booking_quote_skill_node)

__all__ = ["BookingQuoteSkill", "travel_booking_quote_skill_node"]
