"""infra.cache.backend — 缓存抽象层。

设计：
- CacheBackend ABC：统一接口
- InMemoryCache：线程安全的 dict + TTL（从三处手写 cache 提取）
- TwoTierCache：L1 InMemoryCache（短 TTL）+ L2 Redis（完整 TTL）
- get_cache()：Redis 可用返回 TwoTierCache，否则 InMemoryCache
"""
from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

from backend.shared.logger import logger


class CacheBackend(ABC):
    """缓存后端抽象接口。"""

    @abstractmethod
    def get_json(self, key: str) -> Any | None:
        """读取 JSON 值。不存在或过期返回 None。"""

    @abstractmethod
    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        """写入 JSON 值。ttl 单位为秒。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除 key。"""

    @abstractmethod
    def incr_version(self, key: str) -> int:
        """原子递增版本号（用于批量失效）。返回新版本号。"""

    def incr(self, key: str, ttl: int | None = None) -> int:
        """原子自增计数器（不存在从 0 开始），返回自增后的值。

        ttl 仅在 key 首次创建（或已过期重建）时设置，续增不重置窗口——
        计数型守卫（如追问防循环）依赖该语义得到「自首次计数起的固定窗口」。
        抽象层给默认实现以兼容既有测试假缓存；两个内置后端各自原子实现。
        """
        current = self.get_json(key)
        new_val = (current if isinstance(current, int) else 0) + 1
        self.set_json(key, new_val, ttl=ttl)
        return new_val


class InMemoryCache(CacheBackend):
    """线程安全的内存缓存（dict + TTL）。"""

    def __init__(self, default_ttl: int = 300):
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get_json(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at > 0 and time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.monotonic() + effective_ttl if effective_ttl > 0 else 0
        with self._lock:
            self._store[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def incr_version(self, key: str) -> int:
        ver_key = f"__ver__:{key}"
        with self._lock:
            entry = self._store.get(ver_key)
            current = entry[0] if entry else 0
            new_ver = current + 1
            self._store[ver_key] = (new_ver, 0)
            return new_ver

    def incr(self, key: str, ttl: int | None = None) -> int:
        with self._lock:
            entry = self._store.get(key)
            current = 0
            expires_at = 0
            if entry is not None:
                value, expires_at = entry
                # 已过期的 key 视同不存在：从 0 重建并重设窗口
                if expires_at <= 0 or time.monotonic() <= expires_at:
                    current = value if isinstance(value, int) else 0
                else:
                    expires_at = 0
            new_val = current + 1
            if ttl is not None and expires_at <= 0:
                expires_at = time.monotonic() + ttl
            self._store[key] = (new_val, expires_at)
            return new_val


class TwoTierCache(CacheBackend):
    """两级缓存：L1 内存（短 TTL）+ L2 Redis（完整 TTL）。"""

    def __init__(self, redis_client, prefix: str, default_ttl: int = 300, l1_ttl: int = 30):
        self._redis = redis_client
        self._prefix = prefix
        self._default_ttl = default_ttl
        self._l1 = InMemoryCache(default_ttl=l1_ttl)

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get_json(self, key: str) -> Any | None:
        val = self._l1.get_json(key)
        if val is not None:
            return val
        try:
            raw = self._redis.get(self._full_key(key))
            if raw is None:
                return None
            val = json.loads(raw)
            self._l1.set_json(key, val)
            return val
        except Exception as e:
            logger.debug(f"[TwoTierCache] redis get failed: {e}")
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._l1.set_json(key, value, ttl=min(effective_ttl, 30))
        try:
            self._redis.set(self._full_key(key), json.dumps(value, ensure_ascii=False), ex=effective_ttl)
        except Exception as e:
            logger.debug(f"[TwoTierCache] redis set failed: {e}")

    def delete(self, key: str) -> None:
        self._l1.delete(key)
        try:
            self._redis.delete(self._full_key(key))
        except Exception as e:
            logger.debug(f"[TwoTierCache] redis delete failed: {e}")

    def incr_version(self, key: str) -> int:
        try:
            return self._redis.incr(self._full_key(f"__ver__:{key}"))
        except Exception as e:
            logger.debug(f"[TwoTierCache] redis incr failed: {e}")
            return self._l1.incr_version(key)

    def incr(self, key: str, ttl: int | None = None) -> int:
        full_key = self._full_key(key)
        try:
            new_val = self._redis.incr(full_key)
            if ttl is not None and self._redis.ttl(full_key) < 0:
                # 仅首建时设窗口，续增不重置（与 InMemoryCache.incr 同语义）
                self._redis.expire(full_key, ttl)
            return new_val
        except Exception as e:
            logger.debug(f"[TwoTierCache] redis incr failed: {e}")
            return self._l1.incr(key, ttl=ttl)


_caches: dict[str, CacheBackend] = {}
_caches_lock = threading.Lock()


def get_cache(name: str, ttl: int = 300) -> CacheBackend:
    """获取命名缓存实例。

    Redis 可用时返回 TwoTierCache，否则降级为 InMemoryCache。
    """
    if name in _caches:
        return _caches[name]

    with _caches_lock:
        if name in _caches:
            return _caches[name]

        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is not None:
            cache = TwoTierCache(r, prefix=f"{REDIS_KEY_PREFIX}cache:{name}:", default_ttl=ttl)
            logger.debug(f"[Cache] {name}: TwoTierCache (Redis + L1)")
        else:
            cache = InMemoryCache(default_ttl=ttl)
            logger.debug(f"[Cache] {name}: InMemoryCache (Redis unavailable)")

        _caches[name] = cache
        return cache
