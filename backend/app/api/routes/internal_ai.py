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
from pydantic import BaseModel, Field

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


# 安全卫生批次（评审 P2，2026-11-10）：租户敏感数据面工具清单（MCP 域
# rag/sql 的全部读端点）；memory 前缀为将来 MCP 暴露预留。空 actor 调用
# 这些工具一律 403（见 call_tool）。
_IDENTITY_SENSITIVE_TOOLS = frozenset({
    "search_knowledge", "list_documents", "sql_query", "list_tables",
})


class AiCallRequest(BaseModel):
    tool: str
    params: dict = {}

    # P3（docs/auth 修复清单③）：Java 侧透传终端用户身份（payload 委托）——
    # 该路由是 Java→Python 直连（不过网关，无身份头），工具层的权限校验与
    # 审计归属靠这里绑定；不传即 anonymous，工具侧 fail-safe。
    # 信任边界（PR#27 收口）：actor 字段的权威性以 X-Internal-Token 为锚
    # （token 持有者=可信内部服务，终端身份由 Java 侧从已验证会话透传，
    # Python 不再二次验签——与网关-后端委托模型一致）。约束仅防注入：
    # 长度上限 + 禁控制字符，防 CRLF 伪造审计日志行；真实 Java 调用方
    # 尚未接入（AI_TOOLS_ENABLED 默认 false），格式收紧不影响现网。
    actor_user_id: str = Field("", max_length=128,
                               pattern=r"^[^\r\n\x00-\x1f]*$")
    actor_department: str = Field("", max_length=128,
                                  pattern=r"^[^\r\n\x00-\x1f]*$")


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

    from backend.core.request_context import (
        set_tool_department,
        set_tool_permissions,
        set_tool_roles,
        set_tool_tenant_id,
        set_tool_user_id,
    )
    from backend.observability.tracer import current_trace_context
    from mcp_servers.manager import manager

    # 审计 #38：actor 必须解析成启用中的真实用户，并绑定其权威租户；
    # 此前直接把请求体 id 当身份、租户置空（归一 default 桶），
    # 令牌持有者可指定任意 user_id 读取该用户长期记忆。
    from backend.security.task_authorization import (
        TaskAuthorizationDenied,
        resolve_delegated_actor,
    )

    actor = (req.actor_user_id or "").strip()
    tenant_id = ""
    roles: tuple[str, ...] = ()
    department = req.actor_department
    # 安全卫生批次（评审 P2，2026-11-10）：空 actor 不得执行租户敏感工具。
    # 此前 ``if actor:`` 使空 actor 完全跳过委托身份解析，绑定空 user/空
    # 租户（归一 default 桶）执行数据面工具——审计 #38 封死了"指定任意
    # user_id"，这里补上"不带 actor 以 default 桶执行"的半边。
    if not actor and (req.tool in _IDENTITY_SENSITIVE_TOOLS
                      or req.tool.startswith("memory_")):
        raise HTTPException(
            status_code=403,
            detail={"error": "ActorRequired",
                    "message": (f"工具 {req.tool} 依赖租户敏感数据，"
                                "必须透传终端用户 actor_user_id")},
        )
    if actor:
        try:
            tenant_id, db_department, roles = resolve_delegated_actor(actor)
        except TaskAuthorizationDenied as exc:
            raise HTTPException(
                status_code=403,
                detail={"error": "ActorNotTrusted", "message": str(exc)},
            ) from exc
        department = department or db_department

    set_tool_user_id(actor)
    set_tool_department(department)
    set_tool_tenant_id(tenant_id)
    set_tool_roles(roles)
    set_tool_permissions(None)

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
