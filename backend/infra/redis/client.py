"""infra.redis.client — Redis 单例客户端。

设计：
- 双重检查锁懒初始化
- 连接失败时返回 None，调用方优雅降级
- probe cooldown：连接失败后 60s 内不再重试（避免日志洪泛）
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from backend.shared.logger import logger

# 连接串里的口令绝不进日志（2026-10-12 P0：一次性能取证的日志导出把带明文
# 密码的 Redis URL 带进了公开 PR；根因是这里直接打印了整个连接串）。
# 规则与 observability/redaction 的"敏感字段不外发"一致，只做最小必要脱敏。
_URL_USERINFO_RE = re.compile(r"://([^/@s]+)@")


def safe_url_for_log(url: str | None) -> str:
    """连接串脱敏：去掉 userinfo 口令与 query 中的 password 参数。

    只保留 scheme/host/port/path，认证信息统一替换为 `***`。
    非 URL 形态（None/空/异常）返回空串，绝不回退到原串——回退即泄漏。
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
        if not parts.scheme or not parts.netloc:
            return "***"
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        if parts.username is not None or parts.password is not None:
            netloc = f"***@{host}"
        else:
            netloc = host
        query = "***" if parts.query else ""
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except Exception:  # noqa: BLE001 — 脱敏失败时必须放弃原串
        return "***"

_client = None
_lock = threading.Lock()
_last_probe_fail: float = 0.0
_PROBE_COOLDOWN = 60.0
_auth_client = None
_auth_lock = threading.Lock()
_last_auth_probe_fail: float = 0.0


def get_redis():
    """获取 Redis 客户端单例。

    Returns:
        redis.Redis | None: 连接成功返回客户端，不可用返回 None。
    """
    global _client, _last_probe_fail

    if _client is not None:
        return _client

    now = time.monotonic()
    if now - _last_probe_fail < _PROBE_COOLDOWN:
        return None

    with _lock:
        if _client is not None:
            return _client

        from backend.config.redis import REDIS_ENABLED
        if not REDIS_ENABLED:
            return None

        try:
            import redis
            from backend.config.redis import (
                REDIS_MAX_CONNECTIONS,
                REDIS_SOCKET_TIMEOUT,
                REDIS_URL,
            )
            _client = redis.Redis.from_url(
                REDIS_URL,
                max_connections=REDIS_MAX_CONNECTIONS,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
                decode_responses=True,
                protocol=2,
            )
            _client.ping()
            logger.info("[Redis] connected: %s", safe_url_for_log(REDIS_URL))
            return _client
        except Exception as e:
            _last_probe_fail = time.monotonic()
            _client = None
            logger.warning(f"[Redis] connection failed (cooldown {_PROBE_COOLDOWN}s): {e}")
            return None


def is_redis_available() -> bool:
    """快速检查 Redis 是否可用（不抛异常）。"""
    r = get_redis()
    if r is None:
        return False
    try:
        r.ping()
        return True
    except Exception:
        return False


def get_auth_redis():
    """获取认证控制面 Redis 客户端。

    黑名单与会话闸是安全控制面，必须与可逐出的业务缓存实例分离；
    未配置独立 URL 时由配置层回退到 REDIS_URL，保持本地开发兼容。
    """
    global _auth_client, _last_auth_probe_fail

    if _auth_client is not None:
        return _auth_client

    now = time.monotonic()
    if now - _last_auth_probe_fail < _PROBE_COOLDOWN:
        return None

    with _auth_lock:
        if _auth_client is not None:
            return _auth_client

        from backend.config.redis import AUTH_REDIS_ENABLED
        if not AUTH_REDIS_ENABLED:
            return None

        try:
            import redis
            from backend.config.redis import (
                AUTH_REDIS_URL,
                REDIS_MAX_CONNECTIONS,
                REDIS_SOCKET_TIMEOUT,
            )
            _auth_client = redis.Redis.from_url(
                AUTH_REDIS_URL,
                max_connections=REDIS_MAX_CONNECTIONS,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
                decode_responses=True,
                protocol=2,
            )
            _auth_client.ping()
            logger.info("[AuthRedis] connected: %s", safe_url_for_log(AUTH_REDIS_URL))
            return _auth_client
        except Exception as exc:
            _last_auth_probe_fail = time.monotonic()
            _auth_client = None
            logger.warning(
                "[AuthRedis] connection failed (cooldown %ss): %s",
                _PROBE_COOLDOWN,
                exc,
            )
            return None


def is_auth_redis_available() -> bool:
    """快速检查认证控制面 Redis 是否可用。"""
    client = get_auth_redis()
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception:
        return False
