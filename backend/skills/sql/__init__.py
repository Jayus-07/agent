"""
skills/sql/ — SQL 查询 Skill

能力：sql.query
职责：接收自然语言问题 → 调用 SQLAgent → 返回结构化 SQLResult
禁止：直接访问数据库（通过 SQL Tool）、业务分析（由 BusinessAnalysisSkill 负责）
"""
from backend.skills.sql.models import SQLResult
from backend.skills.sql.skill import SQLSkill, sql_skill_node
from backend.orchestration.tool_registry import tool_registry

tool_registry.register_skill_node("sql_skill", sql_skill_node)

__all__ = ["SQLSkill", "sql_skill_node", "SQLResult"]
