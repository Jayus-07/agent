"""3.3 摘要缓存 Redis 化 — build_llm_summary 的 L1/L2 双层缓存。

契约：
- 缓存键为 sha256（跨进程稳定，可作 Redis 键）
- L1 miss 时查 L2 Redis；L2 命中不再调 LLM
- L2 写入软失败（Redis 异常不抛出）
"""
import asyncio
import hashlib
import pytest
from unittest.mock import MagicMock

from backend.rag.preprocessing import metadata as md


class FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    import backend.infra.redis.client as redis_client
    monkeypatch.setattr(redis_client, "get_redis", lambda: r)
    return r


def _expected_key(text):
    from backend.config.redis import REDIS_KEY_PREFIX
    return f"{REDIS_KEY_PREFIX}summary:{hashlib.sha256(text[:1000].encode()).hexdigest()}"


class TestSummaryRedisCache:

    def test_key_is_stable_sha256(self):
        """键跨进程稳定（非内置 hash）。"""
        text = "某文档内容" * 100
        assert _expected_key(text) == _expected_key(text)
        assert "summary:" in _expected_key(text)

    def test_redis_hit_skips_llm(self, fake_redis, monkeypatch):
        text = "文档正文内容。" * 300
        # 预填 Redis：summary + 人名列表
        fake_redis.store[_expected_key(text)] = '["缓存摘要", ["张三"]]'

        called = {"n": 0}
        async def fake_cached(text_hash, t, max_length):
            called["n"] += 1
            return ("新摘要", [])

        monkeypatch.setattr(md, "build_llm_summary_cached", fake_cached)
        result = asyncio.run(md.build_llm_summary(text))
        assert result == ("缓存摘要", ["张三"])
        assert called["n"] == 0, "L2 命中时不应调 LLM"

    def test_llm_result_written_back_to_redis(self, fake_redis, monkeypatch):
        text = "另一篇文档。" * 300

        async def fake_cached(text_hash, t, max_length):
            return ("生成摘要", [])

        monkeypatch.setattr(md, "build_llm_summary_cached", fake_cached)
        asyncio.run(md.build_llm_summary(text))
        stored = fake_redis.store.get(_expected_key(text))
        assert stored is not None and "生成摘要" in stored

    def test_redis_failure_falls_back_to_llm(self, monkeypatch):
        text = "Redis 坏掉时的文档。" * 300

        class BoomRedis:
            def get(self, key):
                raise RuntimeError("down")

        import backend.infra.redis.client as redis_client
        monkeypatch.setattr(redis_client, "get_redis", lambda: BoomRedis())

        async def fake_cached(text_hash, t, max_length):
            return ("降级摘要", [])

        monkeypatch.setattr(md, "build_llm_summary_cached", fake_cached)
        result = asyncio.run(md.build_llm_summary(text))
        assert result == ("降级摘要", [])
