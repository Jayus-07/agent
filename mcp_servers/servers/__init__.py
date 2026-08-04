"""MCP Servers 包集合

注册入口在 backend.app.server 启动时调用 register_all()。
"""
from mcp_servers.servers.rag import RAGMCPServer
from mcp_servers.servers.sql import SQLMCPServer
from mcp_servers.manager import manager


def register_all():
    """注册所有内置 MCP Server。"""
    manager.register(RAGMCPServer())
    manager.register(SQLMCPServer())


__all__ = ["RAGMCPServer", "SQLMCPServer", "register_all"]