"""middleware/concurrency.py — CPU 保护并发控制中间件

限制同时处理的请求数，防止 CPU 过载关机。

设计：
  - 轻量只读端点（RAG 查询、统计、operations）跳过信号量，不受并发限制
  - 重量端点（LLM 聊天、上传、重索引）受信号量保护
  - 默认最大并发 5，可通过环境变量 MAX_CONCURRENT_REQUESTS 调整
"""
import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import MAX_CONCURRENT_REQUESTS

_request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# 不阻塞的路径前缀（轻量只读 + 系统端点）
_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/rag/operations",   # 操作审计日志（SQLite 只读）
    "/rag/documents",     # 文档列表/详情（SQLite 只读）
    "/rag/stats",         # 统计（SQLite 只读）
    "/rag/chunks",        # Chunk 详情（SQLite 只读）
    "/rag/search",        # 关键词搜索（只读）
    "/rag/knowledge-bases",  # 知识库列表
    "/rag/health",        # 健康检查
    "/rag/memory",        # 记忆查询
    "/memory",            # 记忆模块（只读查询）
    "/data",              # 数据中心（列表查询）
    "/mcp",               # MCP 服务器列表
    "/reports",           # 报告列表（只读）
    "/schedules",         # 定时任务列表
    "/chat/messages",     # 聊天历史（只读查询）
    "/chat/abort",        # 中止请求（控制信号，需立即处理）
)


async def concurrency_limit_middleware(request: Request, call_next):
    """限制同时处理的请求数，防止 CPU 过载关机。

    轻量只读端点直接放行；重量端点受信号量保护。
    """
    path = request.url.path

    # 轻量只读端点：直接放行，不消耗信号量
    if path in _SKIP_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_PREFIXES
    ):
        return await call_next(request)

    # 重量端点：通过信号量限流
    # 非阻塞获取信号量 —— 槽位满时直接 503，不排队（避免雪崩）
    if not _request_semaphore.locked():
        async with _request_semaphore:
            return await call_next(request)

    # 槽位被占用 → 返回 503
    return JSONResponse(
        status_code=503,
        content={
            "error": "ServerBusy",
            "detail": f"服务器繁忙，正在处理其他请求（最大并发: {MAX_CONCURRENT_REQUESTS}），请稍后重试",
        },
        headers={"Retry-After": "5"},
    )