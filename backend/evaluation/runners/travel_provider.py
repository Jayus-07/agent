"""Travel Provider Runner — Provider 层探针（STOP J9 §107）。

按任务书 PQ1-PQ8 输出 provider 质量指标：
  PQ1 success_rate      PQ2 cache_hit_rate    PQ3 fallback_rate
  PQ4 (stale 并入 PQ3)  PQ5 latency（EvalResult.duration_ms 聚合）
  PQ6 invalid_response_rate  PQ7 unresolved/not_found rate
  PQ8 route estimate fallback rate（route 探针内的降级占比）

全部探针离线（fake provider 打桩），needs_live=False。真实第三方链路验收
由 scripts/e2e_travel_providers.py 承担（STOP J11）。

用例契约：metadata.probe 指定探针名，expected.status 为期望的 ProviderStatus。
"""
from __future__ import annotations

import threading
import time

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.providers.travel.live.result import ProviderStatus


# =============================================
# 探针实现（每个探针自建打桩环境，finally 恢复）
# =============================================
def _probe_place_success() -> ProviderResult:
    from backend.travel.models.poi import Poi

    poi = Poi(poi_id="lbs_probe1", name="探针点", city="福州",
              lat=26.0, lng=119.3, source="tencent:lbs")
    return _run_place(lambda name, city, required=False: poi, "探针点")


def _probe_place_not_found() -> ProviderResult:
    return _run_place(lambda name, city, required=False: None, "虚构点")


def _probe_place_timeout() -> ProviderResult:
    import time as _t

    def _slow(name, city, required=False):
        _t.sleep(10)
        return None

    return _run_place(_slow, "慢点")


def _probe_place_invalid_coords() -> ProviderResult:
    from backend.travel.models.poi import Poi

    poi = Poi(poi_id="lbs_bad", name="坏点", city="福州",
              lat=999.0, lng=119.3, source="tencent:lbs")
    return _run_place(lambda name, city, required=False: poi, "坏点")


def _probe_route_success() -> ProviderResult:
    provider = _routing_provider(lambda a, b, c, d: {
        "distance_km": 5.0, "mode": "drive", "minutes": 15,
        "cost_cny": 24.0, "source": "tencent:lbs"})
    return provider.route(26.08, 119.29, 26.10, 119.31)


def _probe_route_timeout() -> ProviderResult:
    import time as _t

    provider = _routing_provider(lambda a, b, c, d: _t.sleep(10))
    return provider.route(26.08, 119.29, 26.11, 119.32)


def _probe_quota_exhausted() -> ProviderResult:
    from backend.providers.travel.live import quota
    from backend.providers.travel.live.tencent import TencentPlaceProvider
    from backend.travel.models.poi import Poi

    quota.reset_local_counters()
    orig_budget, orig_incr = quota.daily_budget, quota._redis_incr
    poi = Poi(poi_id="lbs_q", name="配额点", city="福州",
              lat=26.0, lng=119.3, source="tencent:lbs")
    try:
        quota.daily_budget = lambda p: 1
        quota._redis_incr = lambda key: None
        provider = TencentPlaceProvider()
        provider._live_map = type("M", (), {
            "resolve_place": staticmethod(
                lambda name, city, required=False: poi)})()
        # 两次占满预算，第三次被软停
        probe_name = f"配额探针{threading.get_ident()}"
        provider.resolve_poi(probe_name + "a", "福州")
        provider.resolve_poi(probe_name + "b", "福州")
        return provider.resolve_poi(probe_name + "c", "福州")
    finally:
        quota.daily_budget = orig_budget
        quota._redis_incr = orig_incr
        quota.reset_local_counters()


def _probe_cache_hit() -> ProviderResult:
    from backend.travel.models.poi import Poi

    poi = Poi(poi_id="lbs_cache", name="缓存点", city="福州",
              lat=26.0, lng=119.3, source="tencent:lbs")
    provider = _place_provider(lambda name, city, required=False: poi)
    name = f"缓存探针{threading.get_ident()}"
    provider.resolve_poi(name, "福州")
    return provider.resolve_poi(name, "福州")


def _run_place(resolve_fn, name: str) -> ProviderResult:
    provider = _place_provider(resolve_fn)
    return provider.resolve_poi(f"{name}{threading.get_ident()}", "福州")


def _place_provider(resolve_fn):
    from backend.providers.travel.live.tencent import TencentPlaceProvider

    provider = TencentPlaceProvider()
    provider._live_map = type("M", (), {
        "resolve_place": staticmethod(resolve_fn)})()
    # is_enabled 需要真配置——探针强制启用
    type(provider).is_enabled = lambda self: True
    return provider


def _routing_provider(live_fn):
    from backend.providers.travel.live.tencent import TencentRoutingProvider

    provider = TencentRoutingProvider()
    provider._live_map = type("M", (), {"live_leg": staticmethod(live_fn)})()
    type(provider).is_enabled = lambda self: True
    return provider


_PROBES = {
    "place_success": (_probe_place_success, "success"),
    "place_not_found": (_probe_place_not_found, "not_found"),
    "place_timeout": (_probe_place_timeout, "timeout"),
    "place_invalid_coords": (_probe_place_invalid_coords, "invalid_response"),
    "route_success": (_probe_route_success, "success"),
    "route_timeout": (_probe_route_timeout, "timeout"),
    "quota_exhausted": (_probe_quota_exhausted, "rate_limited"),
    "cache_hit": (_probe_cache_hit, "success"),
}


def _run_travel_provider(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    # 缓存/quota 隔离（与 tests/travel/conftest 同纪律）：探针禁止写共享
    # Redis，也不得被宿主环境的真实日预算影响——实测 .env 配了
    # TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET=1 时全部探针被误软停。
    # quota 探针（P-07）自行临时覆盖预算，不受此默认隔离影响。
    from backend.infra.cache.backend import InMemoryCache
    from backend.providers.travel.live import cache as pcache, quota

    orig_backend = pcache._backend
    orig_budget = quota.daily_budget
    store = InMemoryCache(default_ttl=600)
    pcache._backend = lambda: store
    quota.daily_budget = lambda provider: 0
    try:
        return _run_all_cases(cases)
    finally:
        pcache._backend = orig_backend
        quota.daily_budget = orig_budget


def _run_all_cases(cases: list[TestCase]) -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        probe = (case.metadata or {}).get("probe", "")
        fn, default_expect = _PROBES.get(probe, (None, None))
        if fn is None:
            results.append(EvalResult(
                case_id=case.id, module="travel-provider", status="error",
                expected=case.expected, actual={},
                error_msg=f"未知探针: {probe}"))
            continue
        reasons: list[str] = []
        try:
            result = fn()
            expected_status = (case.expected or {}).get(
                "status", default_expect)
            if result.status.value != expected_status:
                reasons.append(
                    f"status={result.status.value} 期望 {expected_status}")
            if (case.expected or {}).get("freshness") and \
                    result.freshness.value != case.expected["freshness"]:
                reasons.append(
                    f"freshness={result.freshness.value} 期望 "
                    f"{case.expected['freshness']}")
            actual = {"status": result.status.value,
                      "freshness": result.freshness.value,
                      "latency_ms": result.latency_ms,
                      "has_data": result.data is not None}
        except Exception as e:  # noqa: BLE001 — 单 case 失败不拖垮整批
            reasons.append(f"[runner] 异常: {type(e).__name__}: {e}")
            actual = {}
        results.append(EvalResult(
            case_id=case.id, module="travel-provider",
            status="pass" if not reasons else "fail",
            expected=case.expected, actual=actual,
            error_msg="; ".join(reasons) or None,
            duration_ms=int((time.time() - t0) * 1000)))
    return results


register_runner("travel-provider", _run_travel_provider, needs_live=False)
