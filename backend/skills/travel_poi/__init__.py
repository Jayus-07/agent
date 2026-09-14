"""skills/travel_poi — 旅游 POI 检索 Skill 包

import 本包即完成两件事（与其他 Skill 包同一约定）：
  1. 向 orchestration.tool_registry 注册图节点 travel_poi_skill
  2. 由 skills/registry.py 注册 Skill 实例（capability: travel.poi_search）
"""
from backend.orchestration.tool_registry import tool_registry
from backend.skills.travel_poi.skill import TravelPoiSkill, travel_poi_skill_node

tool_registry.register_skill_node("travel_poi_skill", travel_poi_skill_node)

__all__ = ["TravelPoiSkill", "travel_poi_skill_node"]
