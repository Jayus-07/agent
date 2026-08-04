"""熔断器 — R2 下游保护（PR-2.x）。

CLOSED → OPEN → HALF_OPEN 三态状态机:
  - CLOSED: 正常调用，累计失败 N 次后进入 OPEN
  - OPEN: 快速失败（直接抛 CircuitBreakerOpen），timeout 秒后进入 HALF_OPEN
  - HALF_OPEN: 试探 1 次 → 成功恢复 CLOSED / 失败回到 OPEN

用法:
    cb = CircuitBreaker("deepseek", fail_threshold=5, timeout=30)
    try:
        result = cb.call(deepseek_invoke, prompt)
    except CircuitBreakerOpen:
        return fallback_response

参考: Netflix Hystrix / pybreaker / resilience4j
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

from backend.shared.logger import logger

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "closed"           # 正常
    OPEN = "open"               # 熔断中
    HALF_OPEN = "half_open"     # 试探恢复


class CircuitBreakerOpen(Exception):
    """熔断器开路异常 — 调用方应捕获并降级。"""

    def __init__(self, name: str, retry_in: float):
        self.name = name
        self.retry_in = retry_in
        super().__init__(f"[{name}] 熔断器开路，{retry_in:.0f}s 后重试")


@dataclass
class _Stats:
    failures: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = 0.0


class CircuitBreaker:
    """线程安全的熔断器。

    Attributes:
        name: 下游名称（用于日志）
        fail_threshold: 连续失败 N 次触发熔断
        timeout: 熔断后等待 timeout 秒进入半开
    """

    def __init__(self, name: str, fail_threshold: int = 5, timeout: float = 30.0):
        self.name = name
        self.fail_threshold = fail_threshold
        self.timeout = timeout
        self._state = State.CLOSED
        self._stats = _Stats(last_state_change=time.monotonic())
        self._lock = threading.Lock()

    # ── 公开 API ──

    @property
    def state(self) -> State:
        return self._state

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """受熔断保护的调用。

        Raises:
            CircuitBreakerOpen: 熔断器开路
            原异常: fn 执行失败时透传
        """
        self._check_state()
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def stats(self) -> dict:
        """返回熔断器状态（用于 /metrics 或调试）。"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failures": self._stats.failures,
                "threshold": self.fail_threshold,
                "timeout_s": self.timeout,
                "last_failure_s": round(time.monotonic() - self._stats.last_failure_time, 1)
                if self._stats.last_failure_time else None,
            }

    # ── 状态机 ──

    def _check_state(self) -> None:
        """检查是否可以调用。OPEN 状态下检查是否超时进入 HALF_OPEN。"""
        with self._lock:
            if self._state == State.CLOSED:
                return
            if self._state == State.HALF_OPEN:
                return
            # OPEN: 检查是否到试探时间
            elapsed = time.monotonic() - self._stats.last_state_change
            if elapsed >= self.timeout:
                self._transition(State.HALF_OPEN)
                logger.info(
                    f"[CB:{self.name}] OPEN → HALF_OPEN（{elapsed:.1f}s，试探性放行 1 次）"
                )
                return
            raise CircuitBreakerOpen(self.name, self.timeout - elapsed)

    def _on_success(self) -> None:
        """调用成功。HALF_OPEN → CLOSED，或保持 CLOSED。"""
        with self._lock:
            if self._state == State.HALF_OPEN:
                self._transition(State.CLOSED)
                logger.info(f"[CB:{self.name}] HALF_OPEN → CLOSED（恢复）")
            self._stats.failures = 0

    def _on_failure(self, error: Exception) -> None:
        """调用失败。CLOSED/HALF_OPEN 时累计，达阈值进入 OPEN。"""
        with self._lock:
            self._stats.failures += 1
            self._stats.last_failure_time = time.monotonic()
            if self._state == State.HALF_OPEN:
                self._transition(State.OPEN)
                logger.warning(
                    f"[CB:{self.name}] HALF_OPEN 试探失败，回到 OPEN "
                    f"(failures={self._stats.failures}, error={type(error).__name__})"
                )
            elif self._stats.failures >= self.fail_threshold:
                self._transition(State.OPEN)
                logger.warning(
                    f"[CB:{self.name}] CLOSED → OPEN "
                    f"(failures={self._stats.failures}/{self.fail_threshold})"
                )

    def _transition(self, new_state: State) -> None:
        self._state = new_state
        self._stats.last_state_change = time.monotonic()


# ── 预置熔断器实例 ──

# LLM: 默认 5 次连续失败触发熔断，30s 恢复期
llm_circuit_breaker = CircuitBreaker("deepseek", fail_threshold=5, timeout=30.0)

# PostgreSQL: 数据库连接更关键，3 次失败即熔断，60s 恢复期
pg_circuit_breaker = CircuitBreaker("postgresql", fail_threshold=3, timeout=60.0)

# ChromaDB: 向量库可降级（BM25 兜底），阈值宽松
chroma_circuit_breaker = CircuitBreaker("chromadb", fail_threshold=5, timeout=30.0)


def get_all_breakers() -> dict[str, CircuitBreaker]:
    """返回所有熔断器实例（供 /metrics 查询）。"""
    return {
        "deepseek": llm_circuit_breaker,
        "postgresql": pg_circuit_breaker,
        "chromadb": chroma_circuit_breaker,
    }
