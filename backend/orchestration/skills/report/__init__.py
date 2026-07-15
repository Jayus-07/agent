"""report — Report Skill: 数据 + 模板 → Markdown 报告"""
from backend.orchestration.skills.report.skill import ReportSkill, report_skill_node
from backend.orchestration.tool_registry import tool_registry

tool_registry.register_skill_node("report_skill", report_skill_node)

__all__ = ["ReportSkill", "report_skill_node"]
