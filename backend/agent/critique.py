"""critique — 向后兼容 re-export（新代码请用 backend.agent.planner.critique）"""
from backend.agent.planner.critique import critique_node
from backend.prompts.critique import PLAN_CRITIQUE_SYSTEM

__all__ = ["critique_node", "PLAN_CRITIQUE_SYSTEM"]
