"""middleware/auth.py — API Key 认证中间件

安全策略（fail-closed，2026-08-21 P0 加固）:

- 配置了 API_KEY：除 skip 路径外，必须携带 X-API-Key 头（常量时间比较）
- 未配置 API_KEY 且未显式开启 ALLOW_UNAUTHENTICATED：拒绝所有业务请求（503），
  不再静默放行
- ALLOW_UNAUTHENTICATED=true：显式豁免，仅限本地开发调试使用

会话闸（2026-09-16 方案 A，纵深防御层）:

- 主校验在 APISIX gateway-auth（验签 + 黑名单 fail-closed + 会话键检查）；
  本中间件对随请求透传的 Bearer 做**第二道**会话校验：
  签名有效 + jti 会话键存在，否则按 JWT_SESSION_GUARD_MODE 处置。
- JWT_SESSION_GUARD_MODE：off / audit（默认，只记日志）/ enforce（401）
- Redis 不可用时本层放行并告警（网关层已 fail-closed 兜底，避免双写故障面）
- 纯 api-key 通道（无 Bearer）不检查——服务身份的敏感端点治理
  由 deps.require_user_actor 统一守卫承担
"""
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import ALLOW_UNAUTHENTICATED, API_KEY, ENVIRONMENT
from backend.shared.logger import logger

# 不需要认证的路径
_SKIP_AUTH_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    # 服务间内部端点：不走 API Key，由 internal_ai.py 的
    # verify_internal_token 依赖校验 X-Internal-Token
    "/internal",
    # 自建认证公开端点（2026-09-15 拆分：login/refresh/logout/register 本就无凭据）
    "/auth",
    "/sys",
)

# 精确匹配豁免（绝不能进 _SKIP_AUTH_PREFIXES：那里的 startswith 语义
# 会让 "/" 前缀把全部路径都豁免掉，等于关闭鉴权）
_EXACT_SKIP_AUTH = frozenset({"/"})


async def api_key_middleware(request: Request, call_next):
    """API Key 认证中间件。

    - 配置了 API_KEY 时：除 skip 路径外，必须携带 X-API-Key 头
    - 未配置时：fail-closed 拒绝业务请求（503），除非显式设置
      ALLOW_UNAUTHENTICATED=true（仅限本地开发）
    """
    path = request.url.path

    # 跳过系统端点（健康检查、文档、metrics、服务首页）
    if path in _EXACT_SKIP_AUTH or path in _SKIP_AUTH_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_AUTH_PREFIXES
    ):
        return await call_next(request)

    # 未配置 API_KEY：fail-closed
    if not API_KEY:
        if ALLOW_UNAUTHENTICATED:
            if ENVIRONMENT == "production":
                logger.error(
                    "[Auth] 生产环境 + ALLOW_UNAUTHENTICATED=true + 无 API_KEY → 拒绝请求"
                    "（defense-in-depth：启动校验应已阻止此配置）"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "ProductionAuthDenied",
                        "detail": (
                            "生产环境不允许无认证访问。请配置 API_KEY 并设置 "
                            "ALLOW_UNAUTHENTICATED=false"
                        ),
                    },
                )
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

    # ── 会话闸（方案 A 纵深防御；主校验在 APISIX gateway-auth）──────────
    result = await _session_guard(request)
    if result is not None:
        return result

    return await call_next(request)


def _session_guard_mode() -> str:
    """生效模式：DB 覆盖层（免重启，15s 内生效）→ env 兜底。

    2026-09-16 动态化：原先仅读 env（改动需重启）。现走 sys_config 服务
    （进程内缓存，零阻塞、fail-closed——DB 异常时维持上次已知值或 env 默认）。
    """
    from backend.services.sys_config import get_mode
    return get_mode("JWT_SESSION_GUARD_MODE")


async def _session_guard(request: Request):
    """Bearer 会话校验：签名有效且 auth:session:{userId}:{jti} 存在。

    返回 None 表示放行（或无需检查）；返回 JSONResponse 表示按 enforce 拒绝。
    - 旧令牌（无 jti）：audit 记日志放行；enforce 拒绝（重新登录即得新令牌）
    - Redis 不可用：放行 + warning（网关层同键检查已 fail-closed 兜底）
    """
    authz = request.headers.get("authorization") or ""
    if authz[:7].lower() != "bearer ":
        return None
    token = authz[7:].strip()
    if not token:
        return None

    from backend.security.local_jwt import session_key, verify_access_token

    payload = verify_access_token(token)
    if payload is None:
        return None  # 签名/exp 问题由网关主校验拒绝，本层不重复判定

    key = session_key(payload)
    if key is None:
        if _session_guard_mode() == "enforce":
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized",
                         "detail": "未认证：令牌缺少会话标识（jti），请重新登录"},
            )
        logger.warning("[SessionGuard] audit 放行无 jti 旧令牌: userId=%s",
                       payload.get("userId"))
        return None

    from backend.infra.redis.client import get_redis
    client = get_redis()
    if client is None:
        logger.warning("[SessionGuard] Redis 不可用，会话校验跳过（网关层兜底）")
        return None
    try:
        alive = client.exists(key) == 1
    except Exception:
        logger.warning("[SessionGuard] 会话键查询异常，跳过（网关层兜底）", exc_info=True)
        return None

    if alive:
        return None
    if _session_guard_mode() == "enforce":
        return JSONResponse(
            status_code=401,
            content={"error": "Unauthorized",
                     "detail": "未认证：会话已失效（登出或被强制下线），请重新登录"},
        )
    logger.warning("[SessionGuard] audit 放行已失效会话: userId=%s jti=%s…",
                   payload.get("userId"), str(payload.get("jti"))[:8])


# 在首次加载模块时打印一次状态
if not API_KEY:
    if ALLOW_UNAUTHENTICATED:
        logger.warning("[Auth] ALLOW_UNAUTHENTICATED=true：API 认证已显式豁免（仅限本地开发调试，生产禁止开启）")
    else:
        logger.error("[Auth] API_KEY 未配置！业务端点已全部拒绝（fail-closed）。设置 API_KEY 环境变量后重启；仅本地开发可显式设置 ALLOW_UNAUTHENTICATED=true")
else:
    logger.info("[Auth] API Key 认证已启用")
