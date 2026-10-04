"""infra.redis.client — Redis 单例客户端。

设计：
- 双重检查锁懒初始化
- 连接失败时返回 None，调用方优雅降级
- probe cooldown：连接失败后 60s 内不再重试（避免日志洪泛）
"""
from __future__ import annotations

import threading
import time

from backend.shared.logger import logger

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
            logger.info(f"[Redis] connected: {REDIS_URL}")
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
            logger.info("[AuthRedis] connected: %s", AUTH_REDIS_URL)
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
