"""cache.incr 原子自增语义测试（2026-10-03 追问防循环守卫的地基）

契约：不存在从 0 起增；ttl 仅首建时设置，续增不重置窗口；过期后重建重设。
TwoTierCache 的 Redis 通道无法在单测起真 Redis，验证 InMemoryCache 的
窗口语义 + TwoTierCache 的降级路径（Redis 抛错 → L1）。
"""
from __future__ import annotations

import time


class TestInMemoryCacheIncr:
    def test_incr_from_zero(self):
        from backend.infra.cache.backend import InMemoryCache

        cache = InMemoryCache()
        assert cache.incr("k", ttl=60) == 1
        assert cache.incr("k", ttl=60) == 2
        assert cache.incr("k", ttl=60) == 3

    def test_incr_ttl_set_once_not_reset(self):
        """续增不得重置窗口（守卫的「自首次计数起固定窗口」依赖此语义）。"""
        from backend.infra.cache.backend import InMemoryCache

        cache = InMemoryCache()
        cache.incr("k", ttl=60)
        first_expires = cache._store["k"][1]
        time.sleep(0.01)
        cache.incr("k", ttl=60)
        assert cache._store["k"][1] == first_expires  # 窗口未被推后

    def test_incr_expired_key_restarts_window(self):
        from backend.infra.cache.backend import InMemoryCache

        cache = InMemoryCache()
        cache._store["k"] = (5, time.monotonic() - 1)  # 已过期，值 5
        assert cache.incr("k", ttl=60) == 1  # 视同不存在，从 0 重建

    def test_get_json_sees_incr_value(self):
        from backend.infra.cache.backend import InMemoryCache

        cache = InMemoryCache()
        cache.incr("k", ttl=60)
        assert cache.get_json("k") == 1


class TestTwoTierCacheIncrFallback:
    def test_redis_down_falls_back_to_l1(self):
        from backend.infra.cache.backend import TwoTierCache

        class _BoomRedis:
            def incr(self, key):
                raise RuntimeError("redis down")

            def ttl(self, key):
                raise RuntimeError("redis down")

        cache = TwoTierCache(_BoomRedis(), prefix="t:")
        assert cache.incr("k", ttl=60) == 1
        assert cache.incr("k", ttl=60) == 2
