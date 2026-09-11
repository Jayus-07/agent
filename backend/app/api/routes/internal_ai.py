"""routes/internal_ai.py — Java→Python AI 能力工具网关。

标准化 Java business-service 对 Python AI 能力的同步调用（REST 工具网关形态）：

  GET  /internal/ai/tools   工具清单（MCPManager.discover()，按白名单过滤）
  POST /internal/ai/call    调用工具，统一响应壳
                            {ok, tool, server, result|error, latency_ms, trace_id}

鉴权：X-Internal-Token（AI_INTERNAL_TOKEN，与 business-service InternalTokenFilter
行为一致：令牌为空 = 本地开发模式跳过校验）。
开关：AI_TOOLS_ENABLED=false 时网关整体关闭（默认，部署验证后再开启）。

协议选型说明：Java 侧调用序列固定、Agent 动态推理均在 Python 侧完成，
故选 REST 而非 MCP 协议传输；保留 tools 清单接口提供运行时发现能力。
"""
from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.shared.logger import logger

router = APIRouter(prefix="/internal/ai", tags=["内部-AI能力网关"])


async def verify_internal_token(request: Request) -> None:
    """路由级鉴权依赖：校验 X-Internal-Token（常量时间比较，防时序侧信道）。"""
    from backend.config.messaging import AI_INTERNAL_TOKEN

    if not AI_INTERNAL_TOKEN:
        return  # 本地开发模式：令牌未配置时跳过（对齐 Java InternalTokenFilter）
    provided = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(
        AI_INTERNAL_TOKEN.encode("utf-8"), provided.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid internal token")


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


@router.get("/tools", dependencies=[Depends(verify_internal_token)])
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


@router.post("/call", dependencies=[Depends(verify_internal_token)])
async def call_tool(req: AiCallRequest, request: Request):
    """调用指定 AI 工具，统一响应壳（与 /mcp/call 兼容但增加延迟与 trace 字段）。"""
    _ensure_gateway_enabled()
    if not req.tool:
        raise HTTPException(status_code=400, detail="tool 不能为空")

    allowed = _allowed_tools()
    if allowed != "*" and req.tool not in allowed:
        logger.warning(f"[InternalAI] tool not whitelisted: {req.tool}")
        raise HTTPException(status_code=403, detail=f"tool not allowed: {req.tool}")

    from backend.observability.tracer import current_trace_context
    from mcp_servers.manager import manager

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
