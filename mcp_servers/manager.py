"""
MCP Manager — 管理 MCP Server 生命周期

提供:
  - MCPServer: Server 基类，声明 name + list_tools() + call_tool()
  - MCPManager: 注册/发现/路由多个 MCPServer
  - manager: 全局单例（在 server.py 启动时 register 各 Server）
"""
from typing import Dict, List, Any

from backend.shared.logger import logger


class MCPServer:
    """MCP Server 基类。子类实现 list_tools() 和 call_tool()。"""
    name: str = ""
    description: str = ""

    def list_tools(self) -> List[dict]:
        """返回该 server 暴露的 tool 列表，每项含 name/description/parameters。"""
        return []

    def call_tool(self, tool_name: str, params: dict) -> Any:
        """调用 tool，返回 dict/str/list 等可 JSON 序列化的结果。"""
        raise NotImplementedError(f"{self.name}.{tool_name} 未实现")


class MCPManager:
    """管理多个 MCPServer 单例。"""

    def __init__(self):
        self._servers: Dict[str, MCPServer] = {}

    def register(self, server: MCPServer):
        if not server.name:
            raise ValueError("Server 必须声明 name")
        if server.name in self._servers:
            logger.warning(f"[MCP] server {server.name} 重复注册，已覆盖")
        self._servers[server.name] = server
        logger.info(f"[MCP] registered server: {server.name} ({server.description})")

    def discover(self) -> List[dict]:
        """发现所有 tool。返回 [{server, name, description, parameters}, ...]"""
        tools = []
        for server_name, server in self._servers.items():
            for tool in server.list_tools():
                tools.append({
                    "server": server_name,
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                })
        return tools

    def list_servers(self) -> List[dict]:
        """列出已注册的 server。"""
        return [
            {"name": s.name, "description": s.description, "tool_count": len(s.list_tools())}
            for s in self._servers.values()
        ]

    def route(self, tool_name: str, params: dict) -> Any:
        """根据 tool_name 路由到对应 server。"""
        for server in self._servers.values():
            tool_names = [t["name"] for t in server.list_tools()]
            if tool_name in tool_names:
                logger.info(f"[MCP] route {tool_name} -> {server.name}")
                try:
                    result = server.call_tool(tool_name, params)
                    return {"ok": True, "tool": tool_name, "server": server.name, "result": result}
                except Exception as e:
                    logger.error(f"[MCP] {tool_name} 调用失败: {e}", exc_info=True)
                    return {"ok": False, "tool": tool_name, "error": str(e)}
        return {"ok": False, "error": f"Tool not found: {tool_name}"}


# 全局单例（在 server.py 启动时 register 各 Server）
manager = MCPManager()