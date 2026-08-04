"""向后兼容 re-export — 已迁至 backend/agents/capability。"""
from backend.agents.capability.base import BaseAgentSkill, register_agent  # noqa: F401
from backend.agents.capability.inventory_analyzer import InventoryAnalyzer  # noqa: F401
