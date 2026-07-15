"""middleware/concurrency.py — CPU 保护并发控制中间件

限制同时处理的请求数，防止 CPU 过载关机。
"""
import asyncio
import os

from fastapi import Request
from fastapi.responses import JSONResponse

# 从环境变量读取最大并发请求数
_MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_REQUESTS", "1"))
_request_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# 不阻塞的路径（健康检查、API 文档等）
_SKIP_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


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