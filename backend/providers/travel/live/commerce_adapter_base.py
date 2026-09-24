"""providers/travel/live/commerce_adapter_base.py — Commerce 搜索执行流（STOP K2-K5）

Hotel/Flight 适配器共享的 Provider 层执行流：**与 tencent.py 同一套冻结设施
（cache/quota/single-flight/timeout budget/telemetry），零第二体系**。抽取成
公共函数是为了 fake 与未来 live 适配器共用同一条路径（G2：统一 Provider
Layer 的证据就是两条适配器走同一执行流）；frozen 的 tencent.py 不动。

语义对齐 tencent.py 冻结口径：
  - 缓存 hit/negative 直接返回；negative 仅 NOT_FOUND（60s）
  - quota「先查后增」；BudgetExhausted → RATE_LIMITED + stale fallback
  - stale-if-error 仅失败路径；observed_at 如实保留
  - TIMEOUT/UNAVAILABLE/RATE_LIMITED/INVALID_RESPONSE **绝不入缓存**
    （不存在「把超时缓存成没结果」的通路，G15）
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Callable

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live import quota, telemetry
from backend.providers.travel.live.result import (
    Freshness,
    ProviderResult,
    ProviderStatus,
    failure,
    success,
)

# loader 抛出的语义信号（适配器把底层异常归类后抛出，单一分类点）
class CommerceSearchSignal(Exception):
    """loader 语义信号：携带最终 ProviderStatus（NOT_FOUND/UNAVAILABLE/...）。"""

    def __init__(self, status: ProviderStatus, message: str = ""):
        super().__init__(message or status.value)
        self.status = status
        self.message = message or status.value


def run_commerce_search(
    *, provider_name: str, operation: str, key: str,
    is_enabled: bool, loader: Callable[[], list],
    record_factory: Callable[[dict], object],
) -> ProviderResult[list]:
    """一次 commerce 搜索的完整 Provider 层执行流。

    loader: 返回 Record 列表（可为空 = SUCCESS+[]）；超时抛 TimeoutError；
    其余失败抛 CommerceSearchSignal。
    record_factory: 缓存 dict → Record（缓存内容损坏按 miss 处理，不降假数据）。
    """
    if not is_enabled:
        telemetry.record_fallback(provider_name, "disabled")
        return failure(ProviderStatus.DISABLED, provider=provider_name,
                       operation=operation, error="commerce provider 未启用")

    # 1) 共享缓存：hit / negative 直接返回
    env, cache_status = pcache.cache_get(key)
    telemetry.record_cache(provider_name, pcache.cache_hit_status(env, cache_status))
    if cache_status == "hit":
        records = _records_from_cached(env.data, record_factory)
        if records is None:
            telemetry.record_cache(provider_name, "miss")  # 损坏按 miss 重查
        else:
            telemetry.event("travel.provider.request", provider=provider_name,
                            operation=operation, status="success", cache="hit")
            return success(records, provider=provider_name, operation=operation,
                           latency_ms=0).with_freshness(Freshness.CACHED,
                                                        env.observed_at)
    if cache_status == "negative":
        return failure(ProviderStatus.NOT_FOUND, provider=provider_name,
                       operation=operation,
                       error="negative cache（近期确认无匹配）")

    # 2) quota 软预算（先查后增：被拒调用不消耗计数）
    try:
        quota.check_and_consume(provider_name)
    except quota.BudgetExhausted as e:
        telemetry.record_quota(provider_name)
        telemetry.event("travel.provider.quota_exhausted", provider=provider_name)
        return _stale_or(key, ProviderStatus.RATE_LIMITED, str(e), 0,
                         provider_name, operation, record_factory)

    # 3) single-flight + timeout budget（loader 内先重查缓存：leader 可能刚写入）
    def _load():
        env2, st2 = pcache.cache_get(key)
        if st2 == "hit":
            cached = _records_from_cached(env2.data, record_factory)
            if cached is not None:
                return cached
        return loader()

    t0 = time.monotonic()
    try:
        records, _elected = _single_flight_call(key, operation, _load)
    except TimeoutError:
        latency = int((time.monotonic() - t0) * 1000)
        telemetry.record_request(provider_name, operation, "timeout", latency)
        telemetry.event("travel.provider.timeout", provider=provider_name)
        return _stale_or(key, ProviderStatus.TIMEOUT,
                         f"超出预算（{operation}）", latency,
                         provider_name, operation, record_factory)
    except CommerceSearchSignal as e:
        latency = int((time.monotonic() - t0) * 1000)
        telemetry.record_request(provider_name, operation, e.status.value, latency)
        if e.status == ProviderStatus.NOT_FOUND:
            pcache.cache_put_not_found(key, provider=provider_name,
                                       operation=operation,
                                       observed_at=_now())
            telemetry.event("travel.provider.request", provider=provider_name,
                            operation=operation, status="not_found")
            return failure(ProviderStatus.NOT_FOUND, provider=provider_name,
                           operation=operation, error=e.message,
                           latency_ms=latency)
        return _stale_or(key, e.status, e.message, latency,
                         provider_name, operation, record_factory)
    except Exception as e:  # noqa: BLE001 — 未归类异常按 UNAVAILABLE（不穿透）
        latency = int((time.monotonic() - t0) * 1000)
        telemetry.record_request(provider_name, operation, "unavailable", latency)
        return _stale_or(key, ProviderStatus.UNAVAILABLE, str(e), latency,
                         provider_name, operation, record_factory)

    latency = int((time.monotonic() - t0) * 1000)

    # 4) SUCCESS（含空结果）：写 fresh 缓存（TTL 按 operation，见 FRESH_TTLS）
    pcache.cache_put_success(
        key, data=[asdict(r) for r in records], provider=provider_name,
        operation=operation, observed_at=_now(),
    )
    telemetry.record_request(provider_name, operation, "success", latency)
    telemetry.event("travel.provider.success", provider=provider_name,
                    operation=operation, latency_ms=latency,
                    offer_count=len(records))
    return success(records, provider=provider_name, operation=operation,
                   latency_ms=latency)


def _stale_or(key: str, status: ProviderStatus, error: str, latency_ms: int,
              provider_name: str, operation: str,
              record_factory: Callable[[dict], object]) -> ProviderResult[list]:
    """失败路径：stale-if-error（§32）——只在此处允许用过期缓存。"""
    stale = pcache.cache_get_stale(key)
    if stale is not None:
        records = _records_from_cached(stale.data, record_factory)
        if records is not None:
            telemetry.record_stale(provider_name)
            telemetry.record_fallback(provider_name, "stale")
            telemetry.event("travel.provider.fallback", provider=provider_name,
                            fallback="stale", underlying=status.value)
            return success(records, provider=provider_name,
                           operation=operation,
                           latency_ms=latency_ms).with_freshness(
                Freshness.STALE, stale.observed_at)
    telemetry.record_request(provider_name, operation, status.value, latency_ms)
    telemetry.record_fallback(provider_name, "unresolved")
    telemetry.event("travel.provider.fallback", provider=provider_name,
                    fallback="unresolved", underlying=status.value)
    return failure(status, provider=provider_name, operation=operation,
                   error=error, latency_ms=latency_ms)


def _records_from_cached(data, record_factory) -> list | None:
    """缓存内容 → Record 列表；结构损坏按 None（miss）处理，绝不降假数据。"""
    if not isinstance(data, list):
        return None
    try:
        return [record_factory(item) for item in data]
    except Exception:  # noqa: BLE001 — 反序列化失败按 miss
        return None


def _single_flight_call(key: str, operation: str, loader):
    from backend.providers.travel.live.resilience import (
        call_with_budget,
        single_flight,
    )

    return single_flight(key, lambda: call_with_budget(operation, loader))


def _now() -> str:
    from backend.providers.travel.facts import now_iso

    return now_iso()
