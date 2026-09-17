"""skills/map — 地图信息查询 Skill 包

import 本包即完成两件事（与其他 Skill 包同一约定）：
  1. 向 orchestration.capability_registry 注册图节点 map_lookup_skill
  2. 由 skills/registry.py 注册 Skill 实例（capability: map.lookup）
"""
from backend.orchestration.capability_registry import tool_registry
from backend.skills.map.skill import MapLookupSkill, map_lookup_skill_node

tool_registry.register_skill_node("map_lookup_skill", map_lookup_skill_node)

__all__ = ["MapLookupSkill", "map_lookup_skill_node"]
