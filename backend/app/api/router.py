"""app/api/router.py — 路由聚合

集中注册所有 API 路由，避免 server.py 直接 import 每个 router。
新增路由时只需在此处添加一行 include_router。
"""
from fastapi import APIRouter

from backend.app.api.routes import (
    chat,
    sql,
    rag,
    report,
    llm,
    observability,
    memory,
    data,
    mcp,
)
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.keyword_routes import router as keyword_router

api_router = APIRouter()

# ── 业务路由 ──────────────────────────────────
api_router.include_router(chat.router)
api_router.include_router(sql.router)
api_router.include_router(rag.router)
api_router.include_router(keyword_router)
api_router.include_router(report.router)
api_router.include_router(llm.router)
api_router.include_router(observability.router)
api_router.include_router(memory.router)
api_router.include_router(data.router)
api_router.include_router(data.assets_router)
api_router.include_router(data.pipeline_router)
api_router.include_router(mcp.router)

# ── 系统路由 ──────────────────────────────────
api_router.include_router(health_router)


__all__ = ["api_router"]