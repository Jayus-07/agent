"""
skills/business_analysis/ — 业务分析 Skill

能力：business.analyze
职责：接收 SQLResult → RAG 检索业务知识 → LLM 生成 BusinessInsight
禁止：直接访问数据库（通过前置 sql.query 的 SQLResult 获取数据）
"""

from backend.skills.business_analysis.models import BusinessInsight
from backend.skills.business_analysis.skill import BusinessAnalysisSkill, business_analysis_skill_node

__all__ = ["BusinessAnalysisSkill", "business_analysis_skill_node", "BusinessInsight"]
