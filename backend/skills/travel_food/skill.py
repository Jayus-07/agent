"""skills/travel_food/skill.py — 餐饮检索 Skill（收编重构 U4）

Capability: travel.food_search

无状态单点能力（高德商户检索），主图/其他域可复用；偏好过滤（不辣/清淡）
属旅游域 Dining Scout 编排（auxiliary_tasks + L3 记忆），Skill 面只搬运
事实——与 travel_poi 同一注册逻辑。
"""
from backend.skills.base import BaseSkill
from backend.tools.travel.food import food_search_tool


class FoodSearchSkill(BaseSkill):
    name = "travel_food"
    # Tool 返回 JSON 字符串，声明 structured 让边界归一化成 dict
    output_type = "structured"

    @property
    def _tool_fn(self):
        return food_search_tool
