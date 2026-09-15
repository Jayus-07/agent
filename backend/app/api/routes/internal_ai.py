"""routes/internal_ai.py — Java→Python AI 能力工具网关。

标准化 Java business-service 对 Python AI 能力的同步调用（REST 工具网关形态）：

  GET  /internal/ai/tools   工具清单（MCPManager.discover()，按白名单过滤）
  POST /internal/ai/call    调用工具，统一响应壳
                            {ok, tool, server, result|error, latency_ms, trace_id}

鉴权：X-Internal-Token（AI_INTERNAL_TOKEN）。校验逻辑已上提为共享依赖
`backend.app.api.deps.require_internal_token`（2026-09-15 S0-4），供 prompts 等路由复用。
**fail-closed**：令牌未配置时不再静默跳过 —— 生产环境一律拒绝；仅当显式设置
ALLOW_UNAUTHENTICATED=true（本地开发）才放行。
开关：AI_TOOLS_ENABLED=false 时网关整体关闭（默认，部署验证后再开启）。

协议选型说明：Java 侧调用序列固定、Agent 动态推理均在 Python 侧完成，
故选 REST 而非 MCP 协议传输；保留 tools 清单接口提供运行时发现能力。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.app.api.deps import require_internal_token
from backend.shared.logger import logger

router = APIRouter(prefix="/internal/ai", tags=["内部-AI能力网关"])


def _allowed_tools() -> list[str] | str:
    """解析工具白名单配置。"*" 表示不限制。"""
    from backend.config.messaging import AI_TOOLS_ALLOWED

    names = [n.strip() for n in AI_TOOLS_ALLOWED.split(",") if n.strip()]
    if "*" in names:
        return "*"
    return names


def _ensure_gateway_enabled() -> None:
    from backend.config.messaging import AI_TOOLS_ENABLED

    if not AI_TOOLS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="AI tools gateway disabled (AI_TOOLS_ENABLED=false)",
        )


@router.get("/tools", dependencies=[Depends(require_internal_token)])
async def list_tools():
    """列出 Java 可调用的 AI 工具清单（按白名单过滤）。"""
    _ensure_gateway_enabled()
    from mcp_servers.manager import manager

    allowed = _allowed_tools()
    tools = manager.discover()
    if allowed != "*":
        tools = [t for t in tools if t.get("name") in allowed]
    return {"count": len(tools), "tools": tools}


class AiCallRequest(BaseModel):
    tool: str
    params: dict = {}
    # P3（docs/auth 修复清单③）：Java 侧透传终端用户身份（payload 委托）——
    # 该路由是 Java→Python 直连（不过网关，无身份头），工具层的权限校验与
    # 审计归属靠这里绑定；不传即 anonymous，工具侧 fail-safe。
    actor_user_id: str = ""
    actor_department: str = ""


@router.post("/call", dependencies=[Depends(require_internal_token)])
async def call_tool(req: AiCallRequest, request: Request):
    """调用指定 AI 工具，统一响应壳（与 /mcp/call 兼容但增加延迟与 trace 字段）。"""
    _ensure_gateway_enabled()
    if not req.tool:
        raise HTTPException(status_code=400, detail="tool 不能为空")

    allowed = _allowed_tools()
    if allowed != "*" and req.tool not in allowed:
        logger.warning(f"[InternalAI] tool not whitelisted: {req.tool}")
        raise HTTPException(status_code=403, detail=f"tool not allowed: {req.tool}")

    from backend.core.request_context import set_tool_department, set_tool_user_id
    from backend.observability.tracer import current_trace_context
    from mcp_servers.manager import manager

    # FastAPI 每请求独立 ContextVar 上下文，绑定不会跨请求泄漏
    if req.actor_user_id:
        set_tool_user_id(req.actor_user_id)
    if req.actor_department:
        set_tool_department(req.actor_department)

    started = time.perf_counter()
    result = manager.route(req.tool, req.params)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    trace_id, _ = current_trace_context()
    inbound_trace = request.headers.get("X-Trace-Id", "")

    response = {
        "ok": bool(result.get("ok")),
        "tool": req.tool,
        "server": result.get("server"),
        "latency_ms": latency_ms,
        "trace_id": trace_id or inbound_trace or None,
    }
    if result.get("ok"):
        response["result"] = result.get("result")
    else:
        response["error"] = result.get("error")
    return response
