"""mcp_dynamic — 外部 MCP 动态工具 Skill 包

import 本包即向 orchestration.capability_registry 注册统一执行节点
mcp_dynamic_skill；`mcp.*` capability 的路由映射见
capability_registry.MCP_DYNAMIC_NODE。
"""
from backend.orchestration.capability_registry import tool_registry
from backend.skills.mcp_dynamic.skill import (
    DynamicMcpSkill,
    mcp_dynamic_skill_node,
)

tool_registry.register_skill_node("mcp_dynamic_skill", mcp_dynamic_skill_node)

__all__ = ["DynamicMcpSkill", "mcp_dynamic_skill_node"]
