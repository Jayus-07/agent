"""
MCP Manager — 管理 MCP Server 生命周期

提供:
  - MCPServer: Server 基类，声明 name + list_tools() + call_tool()
  - MCPManager: 注册/发现/路由多个 MCPServer
  - manager: 全局单例（在 server.py 启动时 register 各 Server）
"""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict, List, Any

from backend.shared.logger import logger

# 工具调用执行器：route() 的超时控制依赖线程隔离
# （超时后线程无法强杀，但调用方不再等待，协议端点不被挂死）
_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-tool")


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


class MCPToolError(Exception):
    """MCP 工具受控业务异常——唯一允许透传给调用方的异常类型。

    为什么按显式类型而非 PermissionError/RuntimeError 白名单：异常类型
    不能保证 str(e) 不含敏感数据（任何代码/三方库都可能让任意类型携带
    服务器路径、用户内容或密钥）。只有构造时显式声明 safe_message 与
    code 的本类型才对外透传；其余异常一律映射为稳定错误码 + 通用文案。
    str(e) = "[{code}] {safe_message}"，恒可安全进入日志与响应。
    """

    def __init__(self, safe_message: str, code: str = "tool_failed"):
        super().__init__(f"[{code}] {safe_message}")
        self.safe_message = safe_message
        self.code = code


def _safe_error_location(exc: Exception) -> str:
    """异常发生位置（文件:行号:函数）——安全诊断信息，不含消息原文。"""
    import traceback

    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return "unknown"
    last = tb[-1]
    return f"{last.filename}:{last.lineno}:{last.name}"


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

    def iter_servers(self) -> List[MCPServer]:
        """按注册顺序遍历 server 实例（供协议端点构建工具清单用）。"""
        return list(self._servers.values())

    def route(self, tool_name: str, params: dict) -> Any:
        """根据 tool_name 路由到对应 server（带超时保护）。"""
        from backend.config import MCP_TOOL_TIMEOUT

        for server in self._servers.values():
            tool_names = [t["name"] for t in server.list_tools()]
            if tool_name in tool_names:
                logger.info(f"[MCP] route {tool_name} -> {server.name}")
                try:
                    # STOP D 修复（2026-09-23）：ContextVars 不跨线程——请求
                    # 线程绑定的可信身份（set_tool_user_id/roles 等）在线程
                    # 池里全丢，导致 MCP 通道对合法用户也 fail-closed（D4
                    # 观测验证发现）。显式携带当前 context 执行。
                    import contextvars as _cv

                    _ctx = _cv.copy_context()
                    future = _CALL_EXECUTOR.submit(
                        _ctx.run, server.call_tool, tool_name, params)
                    result = future.result(timeout=MCP_TOOL_TIMEOUT)
                    return {"ok": True, "tool": tool_name, "server": server.name, "result": result}
                except FutureTimeoutError:
                    future.cancel()
                    logger.error(f"[MCP] {tool_name} 调用超时（>{MCP_TOOL_TIMEOUT}s）")
                    return {"ok": False, "tool": tool_name,
                            "error": f"MCP 工具调用超时（>{MCP_TOOL_TIMEOUT}s）"}
                except MCPToolError as e:
                    # 受控业务异常：safe_message 构造时已声明无敏感数据，
                    # 可透传、可落日志（str(e) = "[code] safe_message"）
                    from backend.observability.tracer import current_trace_context

                    trace_id, _ = current_trace_context()
                    logger.error(f"[MCP] {tool_name} 受控失败: {e} "
                                 f"trace={trace_id or '-'}")
                    return {"ok": False, "tool": tool_name,
                            "error": e.safe_message, "error_code": e.code,
                            "trace_id": trace_id or None}
                except Exception as e:
                    # 异常防泄漏（复核修正）：异常类型不能保证 str(e) 无敏感
                    # 数据——一律不透传原文，只回稳定错误码 + 通用文案 +
                    # trace_id；日志记录类型与发生位置（不含消息原文），
                    # 保留安全诊断能力。
                    from backend.observability.tracer import current_trace_context

                    trace_id, _ = current_trace_context()
                    logger.error(
                        f"[MCP] {tool_name} 调用失败 {type(e).__name__} "
                        f"at {_safe_error_location(e)} "
                        f"trace={trace_id or '-'}（异常消息不出日志/边界）")
                    return {"ok": False, "tool": tool_name,
                            "error": "工具执行失败（内部错误），请稍后重试或联系管理员",
                            "error_code": f"internal:{type(e).__name__}",
                            "trace_id": trace_id or None}
        return {"ok": False, "error": f"Tool not found: {tool_name}"}


# 全局单例（在 server.py 启动时 register 各 Server）
manager = MCPManager()