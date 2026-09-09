"""分布式限流 — Redis Token Bucket（Lua 原子 refill + consume）。

多进程/多实例共享同一 Redis 时保证限流一致性。
Redis 不可用时 fallback 到进程内 LLMRateLimiter。

Key: agent:ratelimit:{scope}:{id}
  - scope=global → 全局限流
  - scope=user   → per-user 限流
"""
from __future__ import annotations

import time

from backend.shared.logger import logger

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

if tokens >= requested then
    tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return 0
end
"""


class RedisTokenBucket:
    """Redis-backed token bucket，Lua 脚本保证 refill + consume 原子性。"""

    def __init__(self, key_prefix: str = "agent:ratelimit"):
        self._key_prefix = key_prefix
        self._script_sha: str | None = None

    def _get_redis(self):
        from backend.infra.redis.client import get_redis
        return get_redis()

    def _ensure_script(self, r) -> str:
        if self._script_sha is not None:
            try:
                r.script_exists([self._script_sha])
                return self._script_sha
            except Exception:
                self._script_sha = None
        self._script_sha = r.script_load(_TOKEN_BUCKET_LUA)
        return self._script_sha

    def acquire(
        self,
        scope: str,
        identity: str,
        capacity: float,
        refill_rate: float,
        tokens: float = 1.0,
    ) -> bool:
        """尝试消费令牌。返回 True 表示成功，False 表示被限流。"""
        r = self._get_redis()
        if r is None:
            return self._fallback_acquire(scope, identity, capacity, refill_rate, tokens)
        try:
            key = f"{self._key_prefix}:{scope}:{identity}"
            sha = self._ensure_script(r)
            result = r.evalsha(
                sha, 1, key,
                capacity, refill_rate, time.time(), tokens,
            )
            return bool(result)
        except Exception as e:
            logger.debug(f"[DistributedRateLimit] Redis 调用失败，fallback: {e}")
            return self._fallback_acquire(scope, identity, capacity, refill_rate, tokens)

    def retry_after(
        self,
        scope: str,
        identity: str,
        capacity: float,
        refill_rate: float,
    ) -> float:
        """估算下次可获取令牌的等待时间（秒）。"""
        r = self._get_redis()
        if r is None:
            return 1.0
        try:
            key = f"{self._key_prefix}:{scope}:{identity}"
            bucket = r.hmget(key, "tokens", "last_refill")
            current_tokens = float(bucket[0]) if bucket[0] else capacity
            last_refill = float(bucket[1]) if bucket[1] else time.time()
            elapsed = max(0, time.time() - last_refill)
            effective_tokens = min(capacity, current_tokens + elapsed * refill_rate)
            if effective_tokens >= 1.0:
                return 0.0
            return (1.0 - effective_tokens) / refill_rate
        except Exception:
            return 1.0

    @staticmethod
    def _fallback_acquire(scope, identity, capacity, refill_rate, tokens) -> bool:
        """Redis 不可用时 fallback 到进程内限流。"""
        from backend.infra.llm.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        user_id = identity if scope == "user" else None
        return limiter.acquire(user_id=user_id)


_distributed_limiter: RedisTokenBucket | None = None


def get_distributed_rate_limiter() -> RedisTokenBucket:
    """获取分布式限流器单例。"""
    global _distributed_limiter
    if _distributed_limiter is None:
        _distributed_limiter = RedisTokenBucket()
    return _distributed_limiter
