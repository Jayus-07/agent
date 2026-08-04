"""MCP API — 暴露工具供外部 Agent 调用

GET  /mcp/tools    列出所有可用工具
GET  /mcp/servers  列出所有已注册的 MCP Server
POST /mcp/call     调用指定工具，body: {tool_name, params}
"""
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
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
async def call_tool(req: CallRequest):
    """调用指定 tool。

    body: {"tool_name": "...", "params": {...}}
    返回: {ok: bool, tool, server, result/error}
    """
    if not req.tool_name:
        raise HTTPException(status_code=400, detail="tool_name 不能为空")
    logger.info(f"[MCP] call {req.tool_name} params={req.params}")
    return manager.route(req.tool_name, req.params)