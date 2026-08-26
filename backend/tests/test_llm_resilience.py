"""test_llm_resilience.py — P1-7 熔断 fallback + 显式重试 单测"""
import asyncio
import time
from unittest.mock import patch

import pytest

import backend.infra.llm.proxy as proxy
from backend.infra.circuit_breaker import CircuitBreaker, State


class FakeTimeoutError(Exception):
    """类型名含 timeout，命中 _TRANSIENT_MARKERS"""
    pass


class FakeAuthError(Exception):
    """非瞬时错误（鉴权失败）"""
    pass


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(proxy, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(proxy, "LLM_RETRY_BACKOFF_BASE", 0.01)
    monkeypatch.setattr(proxy, "LLM_FALLBACK_MODEL", "")
    monkeypatch.setattr(proxy, "LLM_ALLOW_DEGRADED_ANSWER", True)
    yield


class TestRetries:
    def test_transient_error_retried_then_success(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise FakeTimeoutError("boom")
            return "ok"

        assert proxy._call_with_resilience(flaky) == "ok"
        assert calls["n"] == 3

    def test_non_transient_error_no_retry(self):
        calls = {"n": 0}

        def bad():
            calls["n"] += 1
            raise FakeAuthError("401")

        # 非瞬时 + 不允许降级话术 → 原异常抛出
        with patch.object(proxy, "LLM_ALLOW_DEGRADED_ANSWER", False):
            with pytest.raises(FakeAuthError):
                proxy._call_with_resilience(bad)
        assert calls["n"] == 1  # 未重试

    def test_transient_exhausted_degrades_to_answer(self):
        def always_fail():
            raise FakeTimeoutError("always down")

        result = proxy._call_with_resilience(always_fail)
        assert hasattr(result, "content")
        assert "不可用" in result.content

    @pytest.mark.asyncio
    async def test_async_transient_retry(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise FakeTimeoutError("async boom")
            return "ok-async"

        assert await proxy._acall_with_resilience(flaky) == "ok-async"
        assert calls["n"] == 2


class TestCircuitBreakerFallback:
    def _open_breaker(self, breaker):
        breaker._state = State.OPEN
        breaker._stats.last_state_change = time.monotonic()

    def test_open_circuit_falls_back_to_degraded_answer(self):
        cb = CircuitBreaker("test-llm", fail_threshold=5, timeout=30.0)
        self._open_breaker(cb)

        with patch("backend.infra.circuit_breaker.llm_circuit_breaker", cb):
            result = proxy._call_with_resilience(lambda: "never-called")
        assert hasattr(result, "content")  # 降级 AIMessage
        assert "never-called" != result

    def test_open_circuit_uses_fallback_model(self):
        cb = CircuitBreaker("test-llm2", fail_threshold=5, timeout=30.0)
        self._open_breaker(cb)

        class FakeFallback:
            def invoke(self, *a, **kw):
                return "fallback-model-answer"

        with patch("backend.infra.circuit_breaker.llm_circuit_breaker", cb), \
             patch.object(proxy, "_get_fallback_llm", return_value=FakeFallback()):
            result = proxy._call_with_resilience(lambda: "never-called")
        assert result == "fallback-model-answer"

    @pytest.mark.asyncio
    async def test_async_open_circuit_degrades(self):
        cb = CircuitBreaker("test-llm3", fail_threshold=5, timeout=30.0)
        cb._state = State.OPEN
        cb._stats.last_state_change = time.monotonic()

        async def never():
            return "never"

        with patch("backend.infra.circuit_breaker.llm_circuit_breaker", cb):
            result = await proxy._acall_with_resilience(never)
        assert hasattr(result, "content")


class TestTransientDetection:
    def test_markers(self):
        assert proxy._is_transient(FakeTimeoutError())
        assert not proxy._is_transient(FakeAuthError())
        assert proxy._is_transient(Exception("x")) is False  # 泛型异常不算瞬时
