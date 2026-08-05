"""middleware/auth.py — API Key 认证中间件

生产环境必须配置 API_KEY 环境变量。
未配置时（开发模式）打印警告但放行所有请求。
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import API_KEY
from backend.shared.logger import logger

# 不需要认证的路径
_SKIP_AUTH_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)


async def api_key_middleware(request: Request, call_next):
    """API Key 认证中间件。

    - 配置了 API_KEY 时：除 skip 路径外，必须携带 X-API-Key 头
    - 未配置时：开发模式，log 警告但放行
    """
    path = request.url.path

    # 跳过系统端点（健康检查、文档、metrics）
    if path in _SKIP_AUTH_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_AUTH_PREFIXES
    ):
        return await call_next(request)

    # 开发模式：未配置 API_KEY → 警告但放行
    if not API_KEY:
        # 每条请求记一次太多，改成每 300 秒记一次（用简单的 rate limit）
        return await call_next(request)

    # 生产模式：校验 X-API-Key
    client_key = request.headers.get("X-API-Key", "")
    if client_key != API_KEY:
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized", "detail": "无效或缺失 X-API-Key"},
        )

    return await call_next(request)


# 在首次加载模块时打印一次状态
if not API_KEY:
    logger.warning("[Auth] API_KEY 未配置！所有 API 端点公开可访问，请在生产环境设置 API_KEY 环境变量")
else:
    logger.info("[Auth] API Key 认证已启用")
