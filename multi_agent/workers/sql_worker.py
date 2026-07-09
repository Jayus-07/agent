"""向后兼容 re-export（新代码请用 multi_agent.skills.sql_skill）"""
from multi_agent.skills.sql_skill import sql_skill_node as sql_worker_node
__all__ = ["sql_worker_node"]
