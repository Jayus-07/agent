"""
skills/business_analysis/models.py — 业务分析结果数据协议

BusinessInsight 是 BusinessAnalyzer 的产出，由 BusinessAnalysisSkill 写入 step_results。
"""
from pydantic import BaseModel, Field


class BusinessInsight(BaseModel):
    """业务分析洞察 — BusinessAnalyzer 产出"""

    summary: str = Field(description="一句话业务摘要")
    risks: list[str] = Field(default_factory=list, description="识别的风险列表")
    suggestions: list[str] = Field(default_factory=list, description="行动建议列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="分析置信度 (0-1)")
    related_knowledge: list[str] = Field(
        default_factory=list,
        description="引用的 RAG 知识片段（用于可解释性）",
    )
