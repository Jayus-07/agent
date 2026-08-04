"""skills/data_export — Data Export Skill 注册"""
from backend.orchestration.tool_registry import tool_registry
from backend.skills.data_export.skill import data_export_skill_node

tool_registry.register_skill_node("data_export_skill", data_export_skill_node)
