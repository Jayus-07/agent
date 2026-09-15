"""PR-0.4 — LLM 限流器单测。

覆盖:
- 桶容量耗尽时 acquire() 返回 False
- 桶补充速率正确（sleep 后 token 增加）
- 双层限流：global 和 per-user 独立
- stats() 返回合理字典
"""
import time

from backend.infra.llm.rate_limiter import LLMRateLimiter, _Bucket, get_rate_limiter


class TestBucket:
    def test_initial_tokens_equal_capacity(self):
        b = _Bucket(capacity=10, refill_rate=1, tokens=10, last_refill=time.monotonic())
        assert b.tokens == 10

    def test_consume_decreases_tokens(self):
        b = _Bucket(capacity=10, refill_rate=1, tokens=10, last_refill=time.monotonic())
        assert b.try_consume(3)
        assert b.tokens == 7

    def test_insufficient_tokens_returns_false(self):
        b = _Bucket(capacity=2, refill_rate=0, tokens=0, last_refill=time.monotonic())
        assert not b.try_consume()

    def test_refill_over_time(self):
        b = _Bucket(capacity=10, refill_rate=100, tokens=0,
                    last_refill=time.monotonic() - 0.1)  # 0.1s 前
        # 100 * 0.1 = 10 tokens
        assert b.try_consume(5)
        assert b.tokens >= 4  # 至少 5 (10-5) 或略多


class TestLLMRateLimiter:
    def test_default_pass(self):
        rl = LLMRateLimiter(global_qps=10, global_burst=10)
        assert rl.acquire()  # burst 容量足够
        assert rl.acquire()

    def test_global_exhaustion(self):
        """burst 耗尽后 acquire() 必须返回 False（qps 极低，测试窗口内补充可忽略）。"""
        rl = LLMRateLimiter(global_qps=0.001, global_burst=3)
        assert rl.acquire()
        assert rl.acquire()
        assert rl.acquire()
        # burst=3 已耗尽；0.001 qps 补充 1 个令牌需 ~1000s，窗口内必然拒绝
        assert not rl.acquire()
        assert not rl.acquire()

    def test_per_user_isolation(self):
        rl = LLMRateLimiter(global_qps=1000, global_burst=1000,
                            per_user_qps=1, per_user_burst=2)
        assert rl.acquire(user_id="alice")
        assert rl.acquire(user_id="alice")
        # bob 不受 alice 影响
        assert rl.acquire(user_id="bob")

    def test_stats_shape(self):
        rl = LLMRateLimiter(global_qps=10, global_burst=10)
        rl.acquire()
        s = rl.stats()
        assert set(s.keys()) == {"global_tokens", "global_capacity", "user_count", "per_user_qps"}
        assert s["global_capacity"] == 10


class TestGetRateLimiter:
    def test_singleton(self):
        a = get_rate_limiter()
        b = get_rate_limiter()
        assert a is b
