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


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """业务层 HTTPException（如 503/404/422）保持原状态码，不再被吞为 500"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.__class__.__name__, "detail": exc.detail},
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