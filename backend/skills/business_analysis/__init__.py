"""
skills/business_analysis/ — 业务分析 Skill

能力：business.analyze
职责：接收 SQLResult → RAG 检索业务知识 → LLM 生成 BusinessInsight
禁止：直接访问数据库（通过前置 sql.query 的 SQLResult 获取数据）
"""

from backend.orchestration.capability_registry import tool_registry
from backend.skills.business_analysis.models import BusinessInsight
from backend.skills.business_analysis.skill import BusinessAnalysisSkill, business_analysis_skill_node

# 图节点由本包自注册（与其他 Skill 包同一约定，2026-09-16 归一）
tool_registry.register_skill_node("business_analysis_skill", business_analysis_skill_node)

__all__ = ["BusinessAnalysisSkill", "business_analysis_skill_node", "BusinessInsight"]
