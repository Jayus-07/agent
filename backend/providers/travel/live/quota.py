"""providers/travel/live/quota.py — Provider 日预算软治理（STOP J7 §51-§57）

任务书 §54/§55：Provider 有日配额时必须有**本项目侧的软预算**——达到
预算即停止非关键 live 请求，转 cache/estimate/seed，且可观测。宁可自己
先停，不要一直请求直到被第三方硬封（120 当日上限）。

实现：
  - ``TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET``（默认 0 = 不限，兼容单机开发）
  - 计数器跨进程共享：Redis INCR（key 带日期，25h 过期）；Redis 不可用
    退化为进程内计数（单机方向安全，多副本会低估——记 debug 日志）
  - 超预算 → :class:`BudgetExhausted` → 适配器走 fallback matrix 并打
    ``quota_exhausted`` 事件 + ``travel_provider_quota_total`` 指标

免费额度不伪造成本（§57）：只记 request_count，不编造金额。
"""
from __future__ import annotations

import datetime as _dt
import threading

from backend.shared.logger import logger

_QUOTA_PREFIX = "travel_provider:quota"
# 当日计数过期给 25h（跨过午夜仍有余量，日期在键里天然轮换）
_COUNTER_TTL_S = 90000

_local_counters: dict[str, int] = {}
_local_lock = threading.Lock()


class BudgetExhausted(Exception):
    """软预算用尽——调用方必须走 cache/estimate/seed 降级。"""

    def __init__(self, provider: str, budget: int):
        super().__init__(f"{provider} 日预算已用尽（{budget}）")
        self.provider = provider
        self.budget = budget


def daily_budget(provider: str) -> int:
    """provider → 本项目侧日软预算；0 = 不限（开发默认）。"""
    import os

    if provider == "tencent:lbs":
        return int(os.getenv("TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET", "0"))
    # STOP K（K0 §4）：commerce provider 显式登记（新 Provider 接入时在此
    # 加一行；未登记 = 不限，保持「未知 provider 零行为变化」）
    commerce_budget_env = {
        "fake:commerce": "TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET",
    }
    env_name = commerce_budget_env.get(provider)
    if env_name:
        return int(os.getenv(env_name, "0"))
    return 0


def _today_key(provider: str) -> str:
    today = _dt.date.today().isoformat()
    return f"{_QUOTA_PREFIX}:{provider}:{today}"


def _redis_incr(key: str) -> int | None:
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is None:
            return None
        count = int(r.incr(key))
        if count == 1:
            r.expire(key, _COUNTER_TTL_S)
        return count
    except Exception:  # noqa: BLE001 — Redis 故障退化为进程内计数
        logger.debug("[TravelProviderQuota] Redis 计数不可用", exc_info=True)
        return None


def _redis_get(key: str) -> int | None:
    """读当前计数；Redis 不可用返回 None（调用方退化为进程内计数）。"""
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is None:
            return None
        raw = r.get(key)
        return int(raw) if raw else 0
    except Exception:  # noqa: BLE001
        logger.debug("[TravelProviderQuota] Redis 读取不可用", exc_info=True)
        return None


def check_and_consume(provider: str) -> int:
    """消耗一次调用额度并返回当前计数；超预算抛 BudgetExhausted。

    预算为 0（不限）时不做任何计数（零 Redis 往返，热路径零开销）。
    「先查后增」：被拒的调用不消耗计数——软预算的语义是「已放行的
    调用数 ≤ budget」，被拒尝试虚增计数会让窗口内的合法调用也被误停。
    先 GET 后 INCR 存在微小竞态（多副本同时通过 GET 检查），对**软**
    预算可接受（最坏多放行个位数请求，仍有熔断与第三方硬限兜底）。
    """
    budget = daily_budget(provider)
    if budget <= 0:
        return 0

    key = _today_key(provider)
    current = _redis_get(key)
    if current is None:  # Redis 不可用 → 进程内计数
        with _local_lock:
            current = _local_counters.get(key, 0)
        if current >= budget:
            raise BudgetExhausted(provider, budget)
        with _local_lock:
            _local_counters[key] = current + 1
        return current + 1

    if current >= budget:
        raise BudgetExhausted(provider, budget)
    return _redis_incr(key)


def current_usage(provider: str) -> int:
    """当前日计数（health/观测用；不消耗额度）。"""
    budget = daily_budget(provider)
    if budget <= 0:
        return 0
    key = _today_key(provider)
    try:
        from backend.infra.redis.client import get_redis

        r = get_redis()
        if r is not None:
            raw = r.get(key)
            return int(raw) if raw else 0
    except Exception:  # noqa: BLE001
        pass
    with _local_lock:
        return _local_counters.get(key, 0)


def reset_local_counters() -> None:
    """清空进程内计数（仅测试用）。"""
    with _local_lock:
        _local_counters.clear()
