"""core/exceptions.py — 全局异常处理

只兜底"非业务预期"异常（ValueError / RuntimeError / Exception）。
FastAPI 自带 HTTPException 处理：业务层 raise HTTPException(503) 会保持 503 状态码。
这里不拦截 HTTPException，让它走 FastAPI 默认路径。
"""
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.shared.logger import logger
from backend.memory.database import MemoryDatabaseUnavailable


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """业务层 HTTPException（如 503/404/422）保持原状态码，不再被吞为 500"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.__class__.__name__, "detail": exc.detail},
    )


async def memory_db_unavailable_handler(request: Request, exc: MemoryDatabaseUnavailable):
    """记忆库配置缺失/不可用 → 503（而非 500 兜底）。

    完整信息（host/dbname/user）只写日志，不进 HTTP 响应体，避免泄露基础设施细节；
    响应里给出可操作指引，让调用方一眼看出是配置问题而不是"没有数据"。
    """
    logger.error(f"[MemoryDB] {request.method} {request.url.path} → {exc}")
    return JSONResponse(
        status_code=503,
        content={
            "error": "MemoryDatabaseUnavailable",
            "detail": "记忆库不可用：PostgreSQL 连接配置缺失或无效，请检查 .env 中的 PG* 配置（详见服务端日志）",
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    """非业务异常的兜底：记录堆栈 → 返回 500（避免泄露内部信息到 detail）"""
    logger.error(
        f"[Unhandled] {request.method} {request.url.path} → "
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "detail": "服务器内部错误"},
    )