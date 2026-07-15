"""向后兼容 re-export（新代码请用 skills.report.skill）"""
from backend.agent.skills.report.skill import report_skill_node
__all__ = ["report_skill_node"]
