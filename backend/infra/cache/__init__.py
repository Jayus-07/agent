"""infra.cache — 统一缓存抽象层。"""
from backend.infra.cache.backend import (
    CacheBackend,
    InMemoryCache,
    TwoTierCache,
    get_cache,
)

__all__ = ["CacheBackend", "InMemoryCache", "TwoTierCache", "get_cache"]
