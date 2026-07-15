"""FastAPI 服务入口 — 一行启动: uvicorn app.server:app"""
import sys
import os

# 确保项目根路径在 sys.path 中 (server.py 在 backend/app/, 需要 3 层 dirname 到项目根)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.core.exceptions import (
    http_exception_handler,
    global_exception_handler,
)
from backend.app.middleware.concurrency import concurrency_limit_middleware
from backend.shared.logger import logger
from backend.mcp.servers import register_all as register_mcp_servers

app = FastAPI(
    title="Agent Platform API",
    description="LangGraph Multi-Agent 智能问答与报告系统",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── 中间件 ──────────────────────────────────────
app.middleware("http")(concurrency_limit_middleware)

# ── CORS ────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 全局异常处理 ──────────────────────────────────
from starlette.exceptions import HTTPException as StarletteHTTPException
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# ── 注册所有路由（聚合在 api/router.py） ──────────────
app.include_router(api_router)

# ── 注册 MCP Server ─────────────────────────────────
register_mcp_servers()


# ═══════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )