"""MCP API — 暴露工具供外部 Agent 调用

GET  /mcp/tools    列出所有可用工具
GET  /mcp/servers  列出所有已注册的 MCP Server
POST /mcp/call     调用指定工具，body: {tool_name, params}

鉴权（P0-1 收口）：三个端点统一 require_identity——MCP 是任意已注册
tool 的执行面，非公开 tool 无匿名用例，未认证一律 401（与 RAG 业务
路由同一权威语义）。工具内资源级授权由各 MCPServer.call_tool 按
ContextVar 身份执行（rag：RagAuthorization；sql：策略链 fail-closed）。
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.app.api.identity import Identity, require_identity
from mcp_servers.manager import manager
from backend.shared.logger import logger

router = APIRouter(
    prefix="/mcp",
    tags=["MCP"],
    dependencies=[Depends(require_identity)],
)


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


def _params_fingerprint(params: dict) -> str:
    """参数摘要（键名+类型+长度）——用户内容/PII 原文不落日志。"""
    return " ".join(
        f"{k}<{type(v).__name__}:{len(str(v))}>" for k, v in params.items()
    )


@router.post("/call")
async def call_tool(
    req: CallRequest,
    request: Request,
    ident: Identity = Depends(require_identity),
):
    """调用指定 tool。

    body: {"tool_name": "...", "params": {...}}
    返回: {ok: bool, tool, server, result/error}

    P3（docs/auth 修复清单③）：调用前绑定工具层身份（set_tool_user_id /
    set_tool_department）——工具内部的权限校验与审计归属此前拿到的是空串。
    身份来源走 identity.py 单一入口（经网关时即注入头）。
    FastAPI 每请求独立 ContextVar 上下文，绑定不会跨请求泄漏。
    """
    if not req.tool_name:
        raise HTTPException(status_code=400, detail="tool_name 不能为空")
    from backend.core.request_context import (
        set_tool_department,
        set_tool_permissions,
        set_tool_roles,
        set_tool_tenant_id,
        set_tool_user_id,
    )

    # 返修2/5（PR#27 复审）：身份五元组显式全量绑定——网关未声明的维度
    # 也显式写入（None/空覆盖），语义上不存在「继承前值」的形态。
    # FastAPI 每请求独立 task 上下文本就隔离，这里是语义自文档化兜底。
    set_tool_user_id(ident.user_id)
    set_tool_department(ident.department)
    set_tool_tenant_id(ident.tenant_id)
    set_tool_roles(ident.roles or ())
    set_tool_permissions(ident.permissions)
    logger.info(f"[MCP] call {req.tool_name} "
                f"params({_params_fingerprint(req.params)}) "
                f"actor={ident.user_id} tenant={ident.tenant_id or '-'}")
    return manager.route(req.tool_name, req.params)
