"""capability package — Capability 抽象层（Phase 1 Commit 6）

设计：
- BaseCapability: 根抽象（统一接口）
- BaseAgentSkill: Business Agent Skill 基类（推理型）

向后兼容：
- 现有 BaseSkill（在 skills/base.py）保持不变
- 通过 Protocol/属性检测兼容 BaseCapability
"""
from backend.orchestration.capability.base import (
    BaseCapability,
    BaseAgentSkill,
    is_capability,
)

__all__ = ["BaseCapability", "BaseAgentSkill", "is_capability"]