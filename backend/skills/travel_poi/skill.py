"""skills/travel_poi/skill.py — 旅游 POI 检索 Skill

Capability: travel.poi_search

为什么只把这一项注册成主图能力：
  行程生成（travel.plan）**刻意不注册**。它需要「槽位追问 → 骨架分配 →
  排程 → 校验 → 修复」的有状态多步流程，已经由旅游域图承担；再包一个
  Skill 让主图 planner 直接调，就会产生第二套实现，且少了约束校验这道
  安全网 —— 同一件事两个实现、其中一个还没校验，是明确的倒退。
  而 POI 候选检索是**无状态单点能力**，主图（如竞品选址、门店周边分析）
  完全可以复用，注册它不会带来歧义。
"""
from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.tools.travel.poi import travel_poi_search_tool


class TravelPoiSkill(BaseSkill):
    name = "travel_poi"
    # Tool 返回 JSON 字符串，声明 structured 让边界归一化成 dict
    output_type = "structured"

    @property
    def _tool_fn(self):
        return travel_poi_search_tool


async def travel_poi_skill_node(state: dict) -> dict:
    """Skill 节点函数（供主图 Planner 的 DAG 调用）"""
    skill = TravelPoiSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "travel.poi_search")
    logger.info("[TravelPOISkill] step=%s cap=%s", state.get("current_step_id"), cap)
    return await skill.execute(state, step_capability=cap)
