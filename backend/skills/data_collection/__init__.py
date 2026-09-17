"""skills/data_collection — 数据采集 Skill"""
from backend.orchestration.capability_registry import tool_registry
from backend.skills.data_collection.skill import DataCollectionSkill, data_collection_skill_node

# 图节点由本包自注册（与其他 Skill 包同一约定，2026-09-16 归一）
tool_registry.register_skill_node("data_collection_skill", data_collection_skill_node)

__all__ = ["DataCollectionSkill", "data_collection_skill_node"]
