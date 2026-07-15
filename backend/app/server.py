"""FastAPI 服务入口 — 一行启动: uvicorn app.server:app"""
import sys
import os

# 确保项目根路径在 sys.path 中 (server.py 在 backend/app/, 需要 3 层 dirname 到项目根)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import chat, sql, rag, report, llm, observability, memory, data, mcp
from backend.app.core.exceptions import (
    http_exception_handler,
    global_exception_handler,
)
from backend.app.middleware.concurrency import concurrency_limit_middleware
from backend.app.observability.health import router as health_router
from backend.shared.logger import logger
from backend.mcp.servers import register_all as register_mcp_servers

app = FastAPI(
    title="Agent Platform API",
    description="LangGraph Multi-Agent 智能问答与报告系统",
    version="2.0.0",
    docs_url="/docs",   # Swagger UI
    redoc_url="/redoc", # ReDoc
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

# ── 注册路由 ──────────────────────────────────────
app.include_router(chat.router)
app.include_router(sql.router)
app.include_router(rag.router)
app.include_router(report.router)
app.include_router(llm.router)
app.include_router(observability.router)
app.include_router(memory.router)
app.include_router(data.router)
app.include_router(data.assets_router)
app.include_router(data.pipeline_router)
app.include_router(mcp.router)
app.include_router(health_router)

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