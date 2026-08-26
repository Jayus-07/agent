"""skills/competitor_analysis/ — 竞品分析 Skill 包（import 即注册 LangGraph 节点）"""
from backend.orchestration.tool_registry import tool_registry
from backend.skills.competitor_analysis.skill import (
    CompetitorAnalysisSkill,
    competitor_analysis_skill_node,
)

tool_registry.register_skill_node("competitor_analysis_skill", competitor_analysis_skill_node)

__all__ = ["CompetitorAnalysisSkill", "competitor_analysis_skill_node"]
