"""向后兼容 re-export（新代码请用 multi_agent.skills.base）"""
from backend.agent.skills.base import execute_with_retry
__all__ = ["execute_with_retry"]
