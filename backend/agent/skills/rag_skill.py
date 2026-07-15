"""向后兼容 re-export（新代码请用 skills.rag.skill）"""
from backend.agent.skills.rag.skill import rag_skill_node
__all__ = ["rag_skill_node"]
