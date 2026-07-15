"""向后兼容 re-export（新代码请用 skills.sql.skill）"""
from backend.agent.skills.sql.skill import sql_skill_node
__all__ = ["sql_skill_node"]
