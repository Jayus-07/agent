"""mcp_servers/protocol_app.py — 标准 MCP 协议端点（streamable HTTP，端口 8091）

把 manager 里注册的全部 MCPServer（rag/sql）转为标准 MCP 协议暴露，
供 Claude/Cursor 等标准 MCP client 直连。工具执行复用既有
MCPServer.call_tool 逻辑（单一事实来源），RAG 工具在本进程
RAG_MODE=remote 下经 backend/rag/client.py 代理转发 rag-service。

为什么独立成服务而不挂主 app：主 app 用 on_event 注册了大量启动钩子，
Starlette 中传入自定义 lifespan 会跳过全部 on_event 处理器，强行挂载
会破坏现有启动逻辑。独立服务与本项目的工具微服务路线（8081/8082/8090）一致。

鉴权：复用主服务同一 fail-closed 语义（API_KEY / ALLOW_UNAUTHENTICATED），
不 import backend.app.api.middleware.auth（mcp_servers 层不得依赖 API 层），
语义对齐由单测锁定。

审计：每个 tools/call 记录 调用方 key 指纹 / 工具名 / 状态码 / 耗时。

启动:
    python -m uvicorn mcp_servers.protocol_app:app --host 0.0.0.0 --port 8091

client 接入（以 Claude Desktop / Cursor 为例）:
    {"mcpServers": {"agent-platform": {
        "url": "http://<host>:8091/mcp",
        "headers": {"X-API-Key": "<API_KEY>"}}}}
"""
import inspect
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from backend.shared.logger import logger

_MCP_PATH = "/mcp"

# JSON-RPC 请求里值得进审计日志的字段
_AUDIT_METHODS = ("tools/call", "tools/list", "initialize")


def _transport_security():
    """DNS rebinding 防护配置：MCP_ALLOWED_HOSTS 配置则启用 Host 校验。

    SDK 默认仅在 host=localhost 时自动启用防护（白名单只含 127.0.0.1/
    localhost），容器/网关部署下客户端 Host 各异（localhost:8091、
    mcp-service:8091、外网域名），不确定的校验会随机 421。
    显式策略：配置了 MCP_ALLOWED_HOSTS → 启用校验；未配置 → 关闭
    Host 校验（X-API-Key 鉴权仍强制执行，浏览器型 rebinding 攻击
    无法携带自定义头，风险已被鉴权覆盖）。
    """
    import os

    hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    from mcp.server.transport_security import TransportSecuritySettings

    if not hosts:
        # 关键：必须显式关闭而非传 None —— FastMCP Settings.host 默认
        # 127.0.0.1，传 None 会触发其 localhost 自动防护（白名单只有
        # 127.0.0.1/localhost），容器/网关部署下随机 421
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts],
    )


# ==================== 工具包装：MCPServer.call_tool → FastMCP ====================

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _make_tool_fn(server, tool_name: str, parameters: dict):
    """按 list_tools 声明的参数表动态构造带类型签名的工具函数。

    FastMCP 经 inspect.signature 推导 inputSchema，动态 __signature__
    让 MCP client 拿到与旧 HTTP 形状一致的参数定义（含默认值/必填）。
    """
    params = []
    for pname, pdef in parameters.items():
        ann = _TYPE_MAP.get(pdef.get("type", "string"), str)
        if pdef.get("required"):
            param = inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann,
            )
        else:
            param = inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=pdef.get("default"), annotation=Optional[ann],
            )
        params.append(param)

    def _fn(**kwargs):
        return server.call_tool(tool_name, kwargs)

    _fn.__signature__ = inspect.Signature(params)
    return _fn


def build_mcp(mgr, transport_security=None) -> FastMCP:
    """从 manager 构建标准 MCP server（工具清单 = 全部注册 server 之和）。"""
    mcp = FastMCP(
        name="agent-platform",
        instructions="跨境电商 Agent 平台：知识库检索（rag）+ 自然语言 SQL（sql）",
        # 无状态 + 纯 JSON 响应：免会话握手粘性，运维/调试更简单，
        # 协议兼容（服务端 MAY 返回 application/json）
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
    seen: set[str] = set()
    for server in mgr.iter_servers():
        for meta in server.list_tools():
            tname = meta["name"]
            if tname in seen:
                raise ValueError(f"MCP 工具名冲突: {tname}（server={server.name}）")
            mcp.add_tool(
                _make_tool_fn(server, tname, meta.get("parameters") or {}),
                name=tname,
                description=(meta.get("description") or "").strip(),
            )
            seen.add(tname)
    logger.info(f"[MCP-Protocol] 注册 {len(seen)} 个工具: {sorted(seen)}")
    return mcp


# ==================== 应用组装 ====================

def build_protocol_app(mgr=None) -> FastAPI:
    """构建完整 FastAPI 应用（鉴权 + 审计 + MCP 挂载）。

    独立成工厂方法供单测注入自定义 manager。
    """
    from mcp_servers.manager import manager as default_manager
    from mcp_servers.servers import register_all

    if mgr is None:
        register_all()  # 幂等（覆盖式注册）
        mgr = default_manager

    mcp = build_mcp(mgr, transport_security=_transport_security())
    mcp_asgi = mcp.streamable_http_app()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # 挂载的子应用 lifespan 不会被执行，session manager 须在父应用启动
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Agent Platform MCP",
        description="标准 MCP 协议端点（streamable HTTP）",
        version="1.0.0",
        lifespan=_lifespan,
        docs_url=None,
        redoc_url=None,
    )

    # ── 鉴权（fail-closed，语义对齐 backend/app/api/middleware/auth.py）──
    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        from backend.config import ALLOW_UNAUTHENTICATED, API_KEY, ENVIRONMENT

        if request.url.path == "/health":
            return await call_next(request)

        if not API_KEY:
            if ALLOW_UNAUTHENTICATED:
                if ENVIRONMENT == "production":
                    logger.error("[MCP-Auth] 生产环境 + ALLOW_UNAUTHENTICATED → 拒绝")
                    return JSONResponse(status_code=503, content={"error": "ProductionAuthDenied"})
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "AuthNotConfigured",
                    "detail": "服务端未配置 API_KEY，已拒绝请求（fail-closed）",
                },
            )

        client_key = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(client_key.encode("utf-8"), API_KEY.encode("utf-8")):
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "无效或缺失 X-API-Key"},
            )
        return await call_next(request)

    # ── 审计：MCP 调用留痕（key 指纹 / method / tool / 耗时）──
    @app.middleware("http")
    async def mcp_audit_middleware(request: Request, call_next):
        start = time.perf_counter()
        rpc_method, tool = "", ""
        if request.method == "POST" and request.url.path.startswith(_MCP_PATH):
            try:
                body = (await request.body()).decode("utf-8", errors="replace")
                payload = json.loads(body) if body else {}
                rpc_method = payload.get("method", "")
                if rpc_method == "tools/call":
                    tool = (payload.get("params") or {}).get("name", "")
            except Exception:
                pass  # 非 JSON-RPC 体（如探测请求）不阻断，只少记字段
        response = await call_next(request)
        if rpc_method in _AUDIT_METHODS:
            from backend.config import API_KEY

            key_fp = ""
            if API_KEY:
                import hashlib

                key_fp = hashlib.sha256(request.headers.get("X-API-Key", "").encode()).hexdigest()[:8]
            latency = (time.perf_counter() - start) * 1000
            logger.info(
                f"[MCP-Audit] key={key_fp or 'none'} method={rpc_method} tool={tool or '-'} "
                f"status={response.status_code} latency={latency:.0f}ms"
            )
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "mcp-protocol"}

    app.mount("/", mcp_asgi)
    return app


app = build_protocol_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8091)
