"""sql — SQL Skill: 自然语言 → 数据库查询"""
from backend.skills.sql.skill import SQLSkill, sql_skill_node
from backend.orchestration.tool_registry import tool_registry

tool_registry.register_skill_node("sql_skill", sql_skill_node)

__all__ = ["SQLSkill", "sql_skill_node"]
