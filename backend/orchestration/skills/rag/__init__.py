"""rag — RAG Skill: 知识库检索 → 带引用的答案"""
from backend.orchestration.skills.rag.skill import RAGSkill, rag_skill_node
from backend.orchestration.tool_registry import tool_registry

tool_registry.register_skill_node("rag_skill", rag_skill_node)

__all__ = ["RAGSkill", "rag_skill_node"]
