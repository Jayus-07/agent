"""MCP API — 暴露工具供外部 Agent 调用

GET  /mcp/tools    列出所有可用工具
GET  /mcp/servers  列出所有已注册的 MCP Server
POST /mcp/call     调用指定工具，body: {tool_name, params}
"""
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

from mcp_servers.manager import manager
from backend.shared.logger import logger

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/tools")
async def list_tools(server: Optional[str] = None):
    """列出所有 MCP 工具。可按 server 过滤。"""
    tools = manager.discover()
    if server:
        tools = [t for t in tools if t["server"] == server]
    return {"count": len(tools), "tools": tools}


@router.get("/servers")
async def list_servers():
    """列出已注册的 MCP Server。"""
    return {"servers": manager.list_servers()}


class CallRequest(BaseModel):
    tool_name: str
    params: dict = {}


@router.post("/call")
async def call_tool(req: CallRequest, request: Request):
    """调用指定 tool。

    body: {"tool_name": "...", "params": {...}}
    返回: {ok: bool, tool, server, result/error}

    P3（docs/auth 修复清单③）：调用前绑定工具层身份（set_tool_user_id /
    set_tool_department）——工具内部的权限校验与审计归属此前拿到的是空串。
    身份来源走 identity.py 单一入口（经网关时即注入头；legacy 兼容旧调用方）。
    FastAPI 每请求独立 ContextVar 上下文，绑定不会跨请求泄漏。
    """
    if not req.tool_name:
        raise HTTPException(status_code=400, detail="tool_name 不能为空")
    from backend.app.api.identity import resolve_identity
    from backend.core.request_context import set_tool_department, set_tool_user_id

    ident = resolve_identity(request)
    if ident.user_id:
        set_tool_user_id(ident.user_id)
    if ident.department:
        set_tool_department(ident.department)
    logger.info(f"[MCP] call {req.tool_name} params={req.params} "
                f"actor={ident.user_id or 'anonymous'}")
    return manager.route(req.tool_name, req.params)