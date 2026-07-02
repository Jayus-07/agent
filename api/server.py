"""FastAPI 服务入口 — 一行启动: python -m api.server"""
import sys
import os
import asyncio

# 确保项目根路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import chat, sql, rag, report, llm

# ── 并发控制：从环境变量读取最大并发请求数 ──────────
_MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_REQUESTS", "1"))
_request_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# 不阻塞的路径（健康检查、API 文档等）
_SKIP_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}

app = FastAPI(
    title="Agent Platform API",
    description="LangGraph Multi-Agent 智能问答与报告系统",
    version="2.0.0",
    docs_url="/docs",   # Swagger UI
    redoc_url="/redoc", # ReDoc
)

# ── CPU 保护：请求并发控制中间件 ────────────────────
@app.middleware("http")
async def concurrency_limit_middleware(request: Request, call_next):
    """限制同时处理的请求数，防止 CPU 过载关机"""
    if request.url.path in _SKIP_PATHS:
        return await call_next(request)

    # 快速路径：槽位空闲时直接进入
    if not _request_semaphore.locked():
        async with _request_semaphore:
            return await call_next(request)
        # semaphore released here

    # 槽位被占用 → 返回 503，不排队等待（避免雪崩）
    return JSONResponse(
        status_code=503,
        content={
            "error": "ServerBusy",
            "detail": f"服务器繁忙，正在处理其他请求（最大并发: {_MAX_CONCURRENT}），请稍后重试",
        },
        headers={"Retry-After": "5"},
    )

# ── CORS ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 全局异常处理 ──────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(type(exc).__name__), "detail": str(exc)},
    )

# ── 注册路由 ──────────────────────────────────────
app.include_router(chat.router)
app.include_router(sql.router)
app.include_router(rag.router)
app.include_router(report.router)
app.include_router(llm.router)


# ── 健康检查 ──────────────────────────────────────
@app.get("/health", tags=["系统"])
async def health():
    from api.deps import get_rag_status
    return {
        "status": "ok",
        "rag": get_rag_status(),
    }


# ═══════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,   # 生产环境关闭热重载
    )
