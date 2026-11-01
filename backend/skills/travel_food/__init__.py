"""skills/travel_food — 餐饮检索 Skill 包

import 本包即完成两件事（与其他 Skill 包同一约定）：
  1. 向 orchestration.capability_registry 注册图节点 travel_food_skill
  2. 由 skills/registry.py 注册 Skill 实例（capability: travel.food_search）
"""
from backend.orchestration.capability_registry import tool_registry
from backend.skills.travel_food.skill import FoodSearchSkill


async def travel_food_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）。"""
    skill = FoodSearchSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get(
        "capability", "travel.food_search")
    return await skill.execute(state, step_capability=cap)


tool_registry.register_skill_node("travel_food_skill", travel_food_skill_node)

__all__ = ["FoodSearchSkill", "travel_food_skill_node"]
