"""middleware/auth.py — API Key 认证中间件

安全策略（fail-closed，2026-08-21 P0 加固）:

- 配置了 API_KEY：除 skip 路径外，必须携带 X-API-Key 头（常量时间比较）
- 未配置 API_KEY 且未显式开启 ALLOW_UNAUTHENTICATED：拒绝所有业务请求（503），
  不再静默放行
- ALLOW_UNAUTHENTICATED=true：显式豁免，仅限本地开发调试使用
"""
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import ALLOW_UNAUTHENTICATED, API_KEY
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
    - 未配置时：fail-closed 拒绝业务请求（503），除非显式设置
      ALLOW_UNAUTHENTICATED=true（仅限本地开发）
    """
    path = request.url.path

    # 跳过系统端点（健康检查、文档、metrics）
    if path in _SKIP_AUTH_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_AUTH_PREFIXES
    ):
        return await call_next(request)

    # 未配置 API_KEY：fail-closed
    if not API_KEY:
        if ALLOW_UNAUTHENTICATED:
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={
                "error": "AuthNotConfigured",
                "detail": (
                    "服务端未配置 API_KEY，已拒绝请求（fail-closed）。"
                    "请设置 API_KEY 环境变量；仅本地开发可显式设置 "
                    "ALLOW_UNAUTHENTICATED=true"
                ),
            },
        )

    # 生产模式：校验 X-API-Key（常量时间比较，防时序侧信道）
    client_key = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(client_key.encode("utf-8"), API_KEY.encode("utf-8")):
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized", "detail": "无效或缺失 X-API-Key"},
        )

    return await call_next(request)


# 在首次加载模块时打印一次状态
if not API_KEY:
    if ALLOW_UNAUTHENTICATED:
        logger.warning("[Auth] ALLOW_UNAUTHENTICATED=true：API 认证已显式豁免（仅限本地开发调试，生产禁止开启）")
    else:
        logger.error("[Auth] API_KEY 未配置！业务端点已全部拒绝（fail-closed）。设置 API_KEY 环境变量后重启；仅本地开发可显式设置 ALLOW_UNAUTHENTICATED=true")
else:
    logger.info("[Auth] API Key 认证已启用")
