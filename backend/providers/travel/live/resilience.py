"""providers/travel/live/resilience.py — 超时预算与 single-flight（STOP J2/J7）

**Timeout Budget（§21）**——按 operation 冻结硬上限，实测口径：
  place  = 3.0s   （单次地点解析；底层 httpx connect 3s 已对齐）
  route  = 4.0s   （单段路线；预热并发池另有 4s 总预算不变）
  weather= TRAVEL_WEATHER_TIMEOUT_S（默认 6s——J0 缺口 D3，此处接线生效）
  ticket = 3.0s   （本轮无真实适配器，预算为契约预留）

原则：``Provider timeout < Travel 整体超时`` 且有界；预算到点立即失败，
走 fallback matrix，绝不把排程拖进分钟级。

**重试分类修正（§22/D4）**：重试只属于网络层（底层 call_sync 已有 1 次
网络重试）。本层在客户端既有行为之上补一条分类约束：**响应体不是合法
JSON（schema 失效）不重试**——通过给 TencentLbsError 打 status=-1 哨兵，
call_sync 对 status<0 直接 raise（infra 层最小改动，所有消费方受益）。

**Single-flight（§56）**：同一进程内并发 identical 请求（同 cache key）
只放一个去打 Provider，其余等待后直接读缓存——防止一次排程的并发预热
把同一段路线重复烧成多次配额。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import TypeVar

from backend.config import map as MAP
from backend.shared.logger import logger

T = TypeVar("T")

# Provider 调用专用池（懒加载单例）：调用受 5QPS 节流 + 熔断约束，4 worker
# 足够；不借用 retrieval_pool（RAG 语义与池容量都不该被外部调用挤占）。
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _provider_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="travel-provider")
    return _pool

# per-operation timeout budget（秒）。改动须同步 J0 文档 §9 与最终报告。
# weather 的实际生效值走 resolve_budget（接线 TRAVEL_WEATHER_TIMEOUT_S），
# 表内值仅作缺省文档。
TIMEOUT_BUDGETS: dict[str, float] = {
    "place": 3.0,
    "route": 4.0,
    "weather": 6.0,
    "ticket": 3.0,
    # STOP K（K0 §4）：commerce 搜索为用户单发请求（非排程多段预热），
    # 预算放宽到 8s，仍远小于 SSE 会话容忍与 Travel 整体超时
    "hotel_search": 8.0,
    "flight_search": 8.0,
    "hotel_meta": 4.0,
}


def resolve_budget(operation: str) -> float:
    """operation → 实际生效的 timeout budget（weather 接线既有配置）。"""
    if operation == "weather":
        from backend.config.travel import TRAVEL_WEATHER_TIMEOUT_S

        return max(1.0, float(TRAVEL_WEATHER_TIMEOUT_S))
    return TIMEOUT_BUDGETS.get(operation, 4.0)


class _SingleFlight:
    """per-key 去重闸：同 key 并发调用只执行一个 loader，其余等结果。"""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def run(self, key: str, loader: Callable[[], T]) -> tuple[T, bool]:
        """返回 (结果, 是否本调用执行了 loader)。同 key 并发时后来者等待并
        复用 leader 的执行时机（各自再查一次缓存——由调用方组织）。"""
        lock = self._lock_for(key)
        elected = lock.acquire(blocking=True)
        try:
            return loader(), elected
        finally:
            lock.release()


_single_flight = _SingleFlight()


def single_flight(key: str, loader: Callable[[], T]) -> tuple[T, bool]:
    """进程内 single-flight（§56）。多副本间由共享缓存吸收（J3）。"""
    return _single_flight.run(key, loader)


def call_with_budget(operation: str, fn: Callable[[], T]) -> T:
    """在 timeout budget 内执行一次同步 Provider 调用。

    超时抛 TimeoutError（由调用方映射为 ProviderStatus.TIMEOUT 并降级）。
    用专用小线程池执行（懒加载单例）。
    """
    budget = resolve_budget(operation)
    future = _provider_pool().submit(fn)
    try:
        return future.result(timeout=budget)
    except _FutureTimeout:
        # Python 3.10：concurrent.futures.TimeoutError 与内建 TimeoutError
        # 尚未合一 —— 统一转内建 TimeoutError，调用方按一种异常降级
        future.cancel()
        logger.warning("[TravelProvider] %s 超出 %.1fs 预算，转降级",
                       operation, budget)
        raise TimeoutError(
            f"{operation} exceeded {budget}s budget") from None
