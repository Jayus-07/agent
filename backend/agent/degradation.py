"""degradation — 向后兼容 re-export（新代码请用 multi_agent.supervisor.degradation）"""
from backend.agent.supervisor.degradation import (
    execute_degradation, can_degrade, get_fallback_capability, DEGRADATION_CHAIN,
)

__all__ = ["execute_degradation", "can_degrade", "get_fallback_capability", "DEGRADATION_CHAIN"]
