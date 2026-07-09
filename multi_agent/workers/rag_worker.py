"""向后兼容 re-export（新代码请用 multi_agent.skills.rag_skill）"""
from multi_agent.skills.rag_skill import rag_skill_node as rag_worker_node
__all__ = ["rag_worker_node"]
