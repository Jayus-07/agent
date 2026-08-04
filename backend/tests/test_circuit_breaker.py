"""R2 熔断器测试 — CLOSED→OPEN→HALF_OPEN 状态机"""
import time

import pytest

from backend.infra.circuit_breaker import (
    CircuitBreaker, CircuitBreakerOpen, State,
    llm_circuit_breaker, pg_circuit_breaker, chroma_circuit_breaker,
    get_all_breakers,
)


class TestStateMachine:
    """核心状态机逻辑"""

    def test_normal_call_passes(self):
        cb = CircuitBreaker("test", fail_threshold=3, timeout=0.5)
        assert cb.state == State.CLOSED
        result = cb.call(lambda x: x * 2, 21)
        assert result == 42

    def test_failures_trigger_open(self):
        cb = CircuitBreaker("test", fail_threshold=3, timeout=0.5)
        fail_count = 0
        for _ in range(5):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                fail_count += 1
            except CircuitBreakerOpen:
                break
        assert cb.state == State.OPEN
        assert fail_count == 3  # 第 3 次失败触发 OPEN

    def test_open_blocks_calls(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.5)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.OPEN
        with pytest.raises(CircuitBreakerOpen) as exc:
            cb.call(lambda: 42)
        assert "test" in str(exc.value)

    def test_half_open_recovery(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.OPEN
        time.sleep(0.4)  # 超过 timeout
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == State.CLOSED  # 恢复

    def test_half_open_failure_returns_to_open(self):
        cb = CircuitBreaker("test", fail_threshold=2, timeout=0.3)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        time.sleep(0.4)  # HALF_OPEN
        try:
            cb.call(lambda: 1 / 0)  # 试探失败
        except ZeroDivisionError:
            pass
        assert cb.state == State.OPEN  # 回到 OPEN
        # 立即调用应被拦截
        with pytest.raises(CircuitBreakerOpen):
            cb.call(lambda: 42)

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", fail_threshold=5, timeout=0.5)
        for _ in range(2):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        cb.call(lambda: 42)  # 成功 — 重置计数
        # 还需要 5 次失败才能触发 OPEN（之前 2 次已重置）
        for _ in range(4):
            try:
                cb.call(lambda: 1 / 0)
            except ZeroDivisionError:
                pass
        assert cb.state == State.CLOSED  # 4 < 5，未触发

    def test_exception_passthrough(self):
        """熔断器透传原始异常（仅拦截 OPEN 状态）"""
        cb = CircuitBreaker("test", fail_threshold=5, timeout=0.5)
        with pytest.raises(ValueError, match="test error"):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("test error")))


class TestPresetBreakers:
    """预置熔断器实例"""

    def test_llm_breaker_config(self):
        assert llm_circuit_breaker.name == "deepseek"
        assert llm_circuit_breaker.fail_threshold == 5
        assert llm_circuit_breaker.state == State.CLOSED

    def test_pg_breaker_config(self):
        assert pg_circuit_breaker.name == "postgresql"
        assert pg_circuit_breaker.fail_threshold == 3  # 数据库更激进
        assert pg_circuit_breaker.timeout == 60.0  # 更长的恢复期

    def test_chroma_breaker_config(self):
        assert chroma_circuit_breaker.name == "chromadb"
        assert chroma_circuit_breaker.state == State.CLOSED

    def test_get_all_breakers(self):
        all_cb = get_all_breakers()
        assert len(all_cb) == 3
        assert set(all_cb.keys()) == {"deepseek", "postgresql", "chromadb"}


class TestStats:
    """stats() 输出"""

    def test_stats_initial(self):
        s = llm_circuit_breaker.stats()
        assert s["name"] == "deepseek"
        assert s["state"] == "closed"
        assert s["failures"] == 0

    def test_stats_after_failure(self):
        cb = CircuitBreaker("test", fail_threshold=5, timeout=1.0)
        try:
            cb.call(lambda: 1 / 0)
        except ZeroDivisionError:
            pass
        s = cb.stats()
        assert s["failures"] == 1
        assert s["last_failure_s"] is not None
