"""熔断器 — R2 下游保护（PR-2.x）。

CLOSED → OPEN → HALF_OPEN 三态状态机:
  - CLOSED: 正常调用，累计失败 N 次后进入 OPEN
  - OPEN: 快速失败（直接抛 CircuitBreakerOpenError），timeout 秒后进入 HALF_OPEN
  - HALF_OPEN: 试探 1 次（其余并发调用快速失败）→ 成功恢复 CLOSED / 失败回到 OPEN

跨进程共享（审查 #13 / docs/architecture/domain-service-map.md#下游熔断）:
  CIRCUIT_BREAKER_SHARED_ENABLED 开启时，fail 计数（INCR+TTL）与 OPEN 状态
  （SET NX+TTL）走 Redis，多 worker/多副本下阈值不再放大 N 倍；Redis 不可用
  自动退回进程内状态（= 单机行为，方向安全）。热路径（CLOSED 下成功的调用）
  零 Redis 往返，轮询节流见 CIRCUIT_BREAKER_SHARED_POLL_SECONDS。

用法:
    cb = CircuitBreaker("deepseek", fail_threshold=5, timeout=30)
    try:
        result = cb.call(deepseek_invoke, prompt)
    except CircuitBreakerOpenError:
        return fallback_response

参考: Netflix Hystrix / pybreaker / resilience4j
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

from backend.shared.logger import logger

T = TypeVar("T")

# 共享态键命名空间（实际键 = {REDIS_KEY_PREFIX}cb:{name}:{fails|open_at}）
_CB_KEY_NS = "cb"


class State(str, Enum):
    CLOSED = "closed"           # 正常
    OPEN = "open"               # 熔断中
    HALF_OPEN = "half_open"     # 试探恢复


class CircuitBreakerOpenError(Exception):
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
        self._probe_in_flight = False  # HALF_OPEN 探测位：仅放行 1 个探测调用（防探测风暴）
        self._lock = threading.Lock()
        # ── 跨进程共享态（仅 CIRCUIT_BREAKER_SHARED_ENABLED=true 时使用）──
        self._last_remote_poll = 0.0        # monotonic，节流 OPEN 广播轮询
        self._redis_degraded_at = 0.0       # unix ts，最近一次 Redis 操作失败时刻
        self._redis_degraded_logged = 0.0   # 降级告警去重（60s 内至多一条）

    # ── 公开 API ──

    @property
    def state(self) -> State:
        return self._state

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """受熔断保护的调用。

        Raises:
            CircuitBreakerOpenError: 熔断器开路
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

    async def acall(self, fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """受熔断保护的异步调用（对称于 call，供 _LLMProxy async wrapper 使用）。

        与 call 共享同一状态机（_check_state/_on_success/_on_failure），
        确保 async 调用与 sync 调用计入同一个熔断统计。

        Raises:
            CircuitBreakerOpenError: 熔断器开路
            原异常: fn 执行失败时透传
        """
        self._check_state()
        try:
            result = await fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def stats(self) -> dict:
        """返回熔断器状态（用于 /metrics 或调试）。"""
        with self._lock:
            info = {
                "name": self.name,
                "state": self._state.value,
                "failures": self._stats.failures,
                "threshold": self.fail_threshold,
                "timeout_s": self.timeout,
                "last_failure_s": round(time.monotonic() - self._stats.last_failure_time, 1)
                if self._stats.last_failure_time else None,
            }
        shared = self._shared_enabled()
        info["shared"] = shared
        if shared:
            info["redis_degraded"] = (
                time.time() - self._redis_degraded_at < 60.0
                if self._redis_degraded_at else False
            )
        return info

    def reset(self) -> None:
        """强制复位到 CLOSED 并清零失败计数（对标 pybreaker/resilience4j）。

        用途：运维手动恢复、测试隔离。生产调用方不要用 reset 掩盖真实故障。
        共享模式下一并清除 Redis 侧计数与 OPEN 广播（全局生效）。
        """
        with self._lock:
            self._state = State.CLOSED
            self._stats = _Stats(last_state_change=time.monotonic())
            self._probe_in_flight = False
        self._redis_clear_remote()

    # ── 跨进程共享态（Redis，全部软失败：异常 = 退回本地单机语义）──

    @staticmethod
    def _shared_enabled() -> bool:
        """调用时读取开关（非导入期），测试可 monkeypatch config 生效。"""
        try:
            from backend.config.redis import CIRCUIT_BREAKER_SHARED_ENABLED
            return bool(CIRCUIT_BREAKER_SHARED_ENABLED)
        except Exception:
            return False

    def _fails_key(self) -> str:
        from backend.config.redis import REDIS_KEY_PREFIX
        return f"{REDIS_KEY_PREFIX or 'agent:'}{_CB_KEY_NS}:{self.name}:fails"

    def _open_key(self) -> str:
        from backend.config.redis import REDIS_KEY_PREFIX
        return f"{REDIS_KEY_PREFIX or 'agent:'}{_CB_KEY_NS}:{self.name}:open_at"

    def _mark_degraded(self, err: Exception) -> None:
        self._redis_degraded_at = time.time()
        if time.time() - self._redis_degraded_logged > 60.0:
            self._redis_degraded_logged = time.time()
            logger.warning(
                "[CB:%s] Redis 共享态不可用，退回进程内状态（60s 内不重复告警）: %s",
                self.name, err,
            )

    def _redis_report_failure(self) -> int | None:
        """失败上报：INCR 计数 + TTL（窗口 = timeout）。返回系统级累计次数。

        仅失败路径调用（成功热路径零 Redis 往返）。返回 None = Redis 不可用。
        """
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
        except Exception as e:  # pragma: no cover - config 层异常视同降级
            self._mark_degraded(e)
            return None
        if r is None:
            return None
        try:
            pipe = r.pipeline()
            pipe.incr(self._fails_key())
            pipe.expire(self._fails_key(), max(int(self.timeout), 1))
            count = int(pipe.execute()[0])
            return count
        except Exception as e:
            self._mark_degraded(e)
            return None

    def _redis_mark_open(self, *, refresh: bool = False) -> None:
        """OPEN 广播：SET open_at EX timeout（首个实例 NX；重开路 refresh 覆盖）。"""
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
        except Exception as e:  # pragma: no cover
            self._mark_degraded(e)
            return
        if r is None:
            return
        try:
            ttl = max(int(self.timeout), 1)
            if refresh:
                r.set(self._open_key(), str(time.time()), ex=ttl)
            else:
                r.set(self._open_key(), str(time.time()), ex=ttl, nx=True)
        except Exception as e:
            self._mark_degraded(e)

    def _redis_clear_remote(self) -> None:
        """恢复 CLOSED / 手动 reset 时清共享态（fail 计数 + OPEN 广播）。"""
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
        except Exception:  # pragma: no cover
            return
        if r is None:
            return
        try:
            r.delete(self._fails_key(), self._open_key())
        except Exception as e:
            self._mark_degraded(e)

    def _poll_remote_state(self) -> None:
        """CLOSED 下按节流周期拉取其它实例的 OPEN 广播；命中即本地转 OPEN。

        本地转 OPEN 用本地时钟重新起算 timeout：远端键 TTL ≤ 本地试探等待，
        探测只会更晚不会更早 —— 保守方向安全。
        """
        now = time.monotonic()
        try:
            from backend.config.redis import CIRCUIT_BREAKER_SHARED_POLL_SECONDS
            interval = CIRCUIT_BREAKER_SHARED_POLL_SECONDS
        except Exception:
            interval = 5.0
        if now - self._last_remote_poll < interval:
            return
        self._last_remote_poll = now
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
        except Exception:  # pragma: no cover
            return
        if r is None:
            return
        try:
            if r.get(self._open_key()):
                with self._lock:
                    if self._state == State.CLOSED:
                        self._transition(State.OPEN)
                        logger.warning(
                            "[CB:%s] 收到其他实例的 OPEN 广播，本地 CLOSED → OPEN", self.name
                        )
        except Exception as e:
            self._mark_degraded(e)

    # ── 状态机 ──

    def _check_state(self) -> None:
        """检查是否可以调用。OPEN 状态下检查是否超时进入 HALF_OPEN。"""
        # 共享模式：CLOSED 下先节流轮询远端 OPEN 广播（锁外 IO，不阻塞热路径持锁）
        if self._shared_enabled() and self._state == State.CLOSED:
            self._poll_remote_state()
        with self._lock:
            if self._state == State.CLOSED:
                return
            if self._state == State.HALF_OPEN:
                # 单探测放行：首个调用已占用探测位（尚未返回），其余并发调用快速失败
                if self._probe_in_flight:
                    raise CircuitBreakerOpenError(self.name, self.timeout)
                self._probe_in_flight = True
                return
            # OPEN: 检查是否到试探时间
            elapsed = time.monotonic() - self._stats.last_state_change
            if elapsed >= self.timeout:
                self._transition(State.HALF_OPEN)
                logger.info(
                    f"[CB:{self.name}] OPEN → HALF_OPEN（{elapsed:.1f}s，试探性放行 1 次）"
                )
                return
            raise CircuitBreakerOpenError(self.name, self.timeout - elapsed)

    def _on_success(self) -> None:
        """调用成功。HALF_OPEN → CLOSED，或保持 CLOSED。"""
        recovered = False
        with self._lock:
            if self._state == State.HALF_OPEN:
                self._transition(State.CLOSED)
                recovered = True
                logger.info(f"[CB:{self.name}] HALF_OPEN → CLOSED（恢复）")
            self._stats.failures = 0
        if recovered:
            # 锁外清共享态：本实例恢复 = 后端已可用，其它实例也应停止快速失败
            self._redis_clear_remote()

    def _on_failure(self, error: Exception) -> None:
        """调用失败。CLOSED/HALF_OPEN 时累计，达阈值进入 OPEN。

        共享模式：失败时先 INCR Redis 计数（锁外 IO），阈值判定取
        max(本地计数, Redis 系统级计数) —— 任一实例看到系统级超阈即熔断。
        """
        remote_count = None
        if self._shared_enabled():
            remote_count = self._redis_report_failure()
        with self._lock:
            self._stats.failures += 1
            self._stats.last_failure_time = time.monotonic()
            if self._state == State.HALF_OPEN:
                self._transition(State.OPEN)
                logger.warning(
                    f"[CB:{self.name}] HALF_OPEN 试探失败，回到 OPEN "
                    f"(failures={self._stats.failures}, error={type(error).__name__})"
                )
                self._redis_mark_open(refresh=True)
            elif max(self._stats.failures, remote_count or 0) >= self.fail_threshold:
                self._transition(State.OPEN)
                logger.warning(
                    f"[CB:{self.name}] CLOSED → OPEN "
                    f"(failures={self._stats.failures}/{self.fail_threshold}"
                    + (f", remote={remote_count}" if remote_count else "")
                    + ")"
                )
                self._redis_mark_open()

    def _transition(self, new_state: State) -> None:
        self._state = new_state
        self._stats.last_state_change = time.monotonic()
        # 探测位随 HALF_OPEN 生命周期管理：转入时占用（触发转换者即探测者），转出时释放
        self._probe_in_flight = new_state == State.HALF_OPEN


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
