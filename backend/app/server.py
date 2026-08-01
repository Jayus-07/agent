"""FastAPI 服务入口 — 一行启动: uvicorn app.server:app"""
import sys
import os

# 确保项目根路径在 sys.path 中 (server.py 在 backend/app/, 需要 3 层 dirname 到项目根)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.exceptions import (
    http_exception_handler,
    global_exception_handler,
)
from backend.app.middleware.concurrency import concurrency_limit_middleware
from backend.shared.logger import logger
from backend.config.rag import RAG_MAX_FILE_SIZE
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


@app.middleware("http")
async def upload_size_limit_middleware(request, call_next):
    """P0-1: 在 endpoint 之前检查 Content-Length, 超过限制直接 413 拒绝.
    避免 FastAPI 等客户端发完整 body 才在 endpoint 拒 (耗带宽/磁盘).
    """
    if request.method == "POST" and "/rag/upload" in str(request.url.path):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > RAG_MAX_FILE_SIZE * 1024 * 1024:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"ok": False, "error": f"file too large (max {RAG_MAX_FILE_SIZE}MB, Content-Length={cl})"},
                status_code=413,
            )
    return await call_next(request)

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
# 启动时后台初始化 RAG Pipeline（避免首次上传等 13 秒）
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def eager_init_rag_pipeline():
    """后台线程预热 RAG pipeline，不阻塞服务启动。"""
    import threading
    def _warmup():
        try:
            from backend.app.api.deps import get_rag_pipeline
            logger.info("[Startup] 后台预热 RAG 管道...")
            get_rag_pipeline()
            logger.info("[Startup] RAG 管道预热完成")
        except Exception as e:
            logger.warning(f"[Startup] RAG 管道预热失败（首次请求会重试）: {e}")
        # 预热 jieba 分词词典（首次加载 ~1s）
        try:
            import jieba
            jieba.initialize()
            logger.info("[Startup] jieba 词典预热完成")
        except Exception:
            pass
    threading.Thread(target=_warmup, daemon=True, name="rag-warmup").start()


# ═══════════════════════════════════════════════════
# 启动时注册 Workflow + 定时任务
# ═══════════════════════════════════════════════════
@app.on_event("startup")
async def register_workflows_and_schedules():
    """注册所有 workflow + 启动定时调度器"""
    try:
        from backend.orchestration.workflow.registry import get_workflow_registry
        from backend.orchestration.workflow.scheduler import get_workflow_scheduler
        from backend.orchestration.workflows.daily_report import DailyReport
        from backend.orchestration.workflows.inventory_alert import InventoryAlert

        reg = get_workflow_registry()
        if reg.get("daily_report") is None:
            reg.register(DailyReport)
        if reg.get("inventory_alert") is None:
            reg.register(InventoryAlert)

        sched = get_workflow_scheduler()
        sched.register_daily("daily_report", hour=9, minute=0)
        sched.register_daily("inventory_alert", hour=8, minute=0)
        sched.start()

        logger.info("[Startup] Workflow 定时任务注册完成")
    except Exception as e:
        logger.warning(f"[Startup] 定时任务注册失败（非致命）: {e}")


# ═══════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )