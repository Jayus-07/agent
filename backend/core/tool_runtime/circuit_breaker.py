"""tool_runtime/circuit_breaker.py — 熔断器（以 tool/service 维度隔离）

CLOSED →（连续失败 ≥ threshold）→ OPEN →（冷却 recovery_seconds）→ HALF_OPEN
  HALF_OPEN：允许少量探测请求；探测成功 → CLOSED，探测失败 → 回 OPEN。

关键约束：每个 tool/service 一个独立 breaker（rag.search 挂掉不能影响
sql.query / order.query / report.generate），注册表全局单例 + 线程锁
（执行链跨多层线程 + thread-local event loop，不能用 asyncio 原语）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _BreakerState:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at_mono: float = 0.0
    half_open_active: int = 0          # HALF_OPEN 下在途探测数
    half_open_successes: int = 0
    opened_count: int = 0              # 累计熔断次数（指标/观测用）


class CircuitBreaker:
    """单 tool/service 维度的熔断器。非协程安全点均由 _lock 保护（微秒级临界区）。"""

    def __init__(
        self,
        key: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 2,
    ):
        self.key = key
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.1, recovery_seconds)
        self.half_open_max = max(1, half_open_max)
        self._lock = threading.Lock()
        self._st = _BreakerState()

    # ── 状态查询 + 许可 ──
    def state(self) -> CircuitState:
        with self._lock:
            return self._refresh_locked().state

    def _refresh_locked(self) -> _BreakerState:
        st = self._st
        if st.state is CircuitState.OPEN:
            if time.monotonic() - st.opened_at_mono >= self.recovery_seconds:
                st.state = CircuitState.HALF_OPEN
                st.half_open_active = 0
                st.half_open_successes = 0
        return st

    def allow_request(self) -> bool:
        """是否放行真实调用；OPEN 下直接 fast fail（不真实访问服务）。"""
        with self._lock:
            st = self._refresh_locked()
            if st.state is CircuitState.CLOSED:
                return True
            if st.state is CircuitState.HALF_OPEN:
                if st.half_open_active < self.half_open_max:
                    st.half_open_active += 1
                    return True
                return False  # 探测名额已满 → 本次 fast fail
            return False

    # ── 结果回填 ──
    def record_success(self) -> None:
        with self._lock:
            st = self._st
            if st.state is CircuitState.HALF_OPEN:
                st.half_open_successes += 1
                st.half_open_active = max(0, st.half_open_active - 1)
                # 探测成功即恢复（探测请求本身就是成功样本）
                if st.half_open_successes >= 1:
                    st.state = CircuitState.CLOSED
                    st.consecutive_failures = 0
                    st.half_open_successes = 0
            else:
                st.consecutive_failures = 0

    def record_failure(self) -> bool:
        """记一次失败。返回值 = 本次是否导致新熔断（供 agent_tool_circuit_open_total）。"""
        tripped = False
        with self._lock:
            st = self._st
            if st.state is CircuitState.HALF_OPEN:
                # 探测失败 → 立即回到 OPEN，重新冷却
                st.state = CircuitState.OPEN
                st.opened_at_mono = time.monotonic()
                st.opened_count += 1
                st.half_open_active = max(0, st.half_open_active - 1)
                st.consecutive_failures = self.failure_threshold
                tripped = True
            elif st.state is CircuitState.CLOSED:
                st.consecutive_failures += 1
                if st.consecutive_failures >= self.failure_threshold:
                    st.state = CircuitState.OPEN
                    st.opened_at_mono = time.monotonic()
                    st.opened_count += 1
                    tripped = True
            # OPEN 状态下的失败不计（真实调用根本没发生）
        return tripped

    def snapshot(self) -> dict:
        with self._lock:
            st = self._refresh_locked()
            return {
                "key": self.key,
                "state": st.state.value,
                "consecutive_failures": st.consecutive_failures,
                "opened_count": st.opened_count,
            }


class CircuitBreakerRegistry:
    """tool key → CircuitBreaker。全局单例；参数取自该 tool 的策略。"""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, key: str, failure_threshold: int = 5,
            recovery_seconds: float = 30.0, half_open_max: int = 2) -> CircuitBreaker:
        with self._lock:
            cb = self._breakers.get(key)
            if cb is None:
                cb = CircuitBreaker(key, failure_threshold, recovery_seconds, half_open_max)
                self._breakers[key] = cb
            return cb

    def reset(self, key: str | None = None) -> None:
        """测试/运维钩子：重置指定或全部熔断器。"""
        with self._lock:
            if key is None:
                self._breakers.clear()
            else:
                self._breakers.pop(key, None)

    def snapshot_all(self) -> dict[str, dict]:
        with self._lock:
            return {k: cb.snapshot() for k, cb in self._breakers.items()}


# 全局注册表（进程内单例）
circuit_registry = CircuitBreakerRegistry()
