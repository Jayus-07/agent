"""向后兼容 re-export（新代码请用 multi_agent.skills.report_skill）"""
from backend.agent.skills.report_skill import report_skill_node as report_worker_node
__all__ = ["report_worker_node"]
