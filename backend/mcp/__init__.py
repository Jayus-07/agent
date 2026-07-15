"""MCP 包 — Model Context Protocol 协议层

Phase 5: 通过 /mcp/tools 和 /mcp/call 端点暴露 Agent 能力，
供外部 Agent（如 Claude/Cursor）调用。
"""
from backend.mcp.manager import MCPServer, MCPManager, manager

__all__ = ["MCPServer", "MCPManager", "manager"]