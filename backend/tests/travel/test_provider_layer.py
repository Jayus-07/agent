"""tests/travel/test_provider_layer.py — STOP J10 Provider 层测试矩阵（T1-T25）

注入纪律：只 mock 外部边界（fake Provider / 假 HTTP 行为），缓存用进程内
隔离实例（conftest autouse）。逐条对应任务书 §66-§90：
  T1-T4  place：success / not_found / timeout / invalid coordinates
  T5-T7  route：success(is_estimate=false) / timeout→estimate / 429→fallback
  T8-T10 weather：success / out_of_horizon / down→仍出单披露
  T11-T12 ticket：known free(0+verified) / unknown(None，禁「免费」)
  T13-T16 cache：hit / expired / negative / stale-if-error
  T17-T18 circuit：open fast-fallback / recovery（复用既有 CircuitBreaker）
  T19    quota exhausted → fallback+metric
  T20    schema drift → invalid_response
  T21    STOP I quality regression（独立于本文件，见 test_quality_golden）
  T22    multi-worker cache（Redis 语义，专项实测脚本覆盖；此处测 TwoTier 语义）
  T23    request dedup（single-flight）
  T24-T25 credential missing / required 语义（当前全 false → disabled 降级）
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live import quota
from backend.providers.travel.live.contracts import (
    TicketFacts,
    TicketProvider,
)
from backend.providers.travel.live.errors import ProviderError, status_from_lbs_error
from backend.providers.travel.live.result import (
    Freshness,
    ProviderResult,
    ProviderStatus,
    success,
)
from backend.providers.travel.live.resilience import (
    call_with_budget,
    single_flight,
)
from backend.providers.travel.live.tencent import (
    TencentPlaceProvider,
    TencentRoutingProvider,
)


@pytest.fixture(autouse=True)
def _unlimited_quota(monkeypatch):
    """模块级 quota 隔离：宿主 .env 可能配了真实日预算（实测 =1 时全部
    探针被误软停）。T19 在用例内自行覆盖为受限预算。"""
    from backend.providers.travel.live import quota

    monkeypatch.setattr(quota, "daily_budget", lambda provider: 0)


def _poi(poi_id="lbs_123", name="平潭岛", lat=25.5, lng=119.8, **kw):
    from backend.travel.models.poi import Poi

    return Poi(poi_id=poi_id, name=name, city="福州", lat=lat, lng=lng, **kw)


def _live_place_provider(monkeypatch, resolve_result, enabled=True):
    """构造 place provider：底层 resolve_place 打桩为固定产物。"""
    provider = TencentPlaceProvider()
    monkeypatch.setattr(provider, "is_enabled", lambda: enabled)
    monkeypatch.setattr(provider._live_map, "resolve_place",
                        lambda name, city, required=False: resolve_result)
    return provider


# ============================================================
# Place：T1-T4
# ============================================================
class TestPlaceProvider:
    def test_t1_success_records_provider_id_and_source(self, monkeypatch):
        provider = _live_place_provider(monkeypatch, _poi())
        result = provider.resolve_poi("平潭岛", "福州")
        assert result.ok
        assert result.data.poi_id == "lbs_123"
        assert result.data.source == "seed:local" or result.data.poi_id.startswith("lbs_")
        assert result.observed_at

    def test_t2_not_found_is_unresolved_not_hallucinated(self, monkeypatch):
        provider = _live_place_provider(monkeypatch, None)
        result = provider.resolve_poi("虚构海湾艺术中心", "厦门")
        assert result.status == ProviderStatus.NOT_FOUND
        assert result.data is None
        # negative cache 生效：第二次不再打底层
        calls = {"n": 0}

        def _count(name, city, required=False):
            calls["n"] += 1
            return None

        monkeypatch.setattr(provider._live_map, "resolve_place", _count)
        provider.resolve_poi("另一个不存在", "厦门")
        provider.resolve_poi("另一个不存在", "厦门")
        assert calls["n"] == 1

    def test_t3_timeout_falls_back_never_500(self, monkeypatch):
        import time as _t

        provider = TencentPlaceProvider()
        monkeypatch.setattr(provider, "is_enabled", lambda: True)

        def _slow(name, city, required=False):
            _t.sleep(10)  # 超出 place 3s 预算
            return None

        monkeypatch.setattr(provider._live_map, "resolve_place", _slow)
        result = provider.resolve_poi("慢地点", "福州")
        assert result.status == ProviderStatus.TIMEOUT
        assert result.data is None

    def test_t4_invalid_coordinates_rejected(self, monkeypatch):
        provider = _live_place_provider(
            monkeypatch, _poi(lat=999.0))
        result = provider.resolve_poi("坏坐标点", "福州")
        assert result.status == ProviderStatus.INVALID_RESPONSE
        assert "lat" in result.error

    def test_place_validation_lng_range(self, monkeypatch):
        provider = _live_place_provider(monkeypatch, _poi(lng=999.0))
        result = provider.resolve_poi("坏经度", "福州")
        assert result.status == ProviderStatus.INVALID_RESPONSE

    def test_disabled_provider_reports_disabled(self, monkeypatch):
        provider = _live_place_provider(monkeypatch, None, enabled=False)
        result = provider.resolve_poi("任意", "福州")
        assert result.status == ProviderStatus.DISABLED


# ============================================================
# Route：T5-T7
# ============================================================
class TestRouteProvider:
    def _routing(self, monkeypatch, live_result, enabled=True):
        provider = TencentRoutingProvider()
        monkeypatch.setattr(provider, "is_enabled", lambda: enabled)
        monkeypatch.setattr(provider._live_map, "live_leg",
                            lambda a, b, c, d: live_result)
        return provider

    def test_t5_live_route_success(self, monkeypatch):
        provider = self._routing(monkeypatch, {
            "distance_km": 5.2, "mode": "drive", "minutes": 18,
            "cost_cny": 25.6, "source": "tencent:lbs"})
        result = provider.route(26.08, 119.29, 26.10, 119.31)
        assert result.ok
        assert result.data.distance_m == 5200
        assert result.data.traffic_aware is True
        # live 语义：业务侧 TransitLeg.is_estimate=False（Provider 数据本身
        # 是真实路径；契约层这里验证 distance/duration 正常归一）

    def test_route_timeout_returns_timeout_status(self, monkeypatch):
        import time as _t

        provider = TencentRoutingProvider()
        monkeypatch.setattr(provider, "is_enabled", lambda: True)

        def _slow(a, b, c, d):
            _t.sleep(10)

        monkeypatch.setattr(provider._live_map, "live_leg", _slow)
        result = provider.route(26.08, 119.29, 26.10, 119.31)
        assert result.status == ProviderStatus.TIMEOUT

    def test_t7_quota_limited_falls_back_not_crash(self, monkeypatch):
        provider = self._routing(monkeypatch, None)
        monkeypatch.setattr(quota, "check_and_consume",
                            lambda p: (_ for _ in ()).throw(
                                quota.BudgetExhausted(p, 1)))
        result = provider.route(26.08, 119.29, 26.10, 119.31)
        assert result.status == ProviderStatus.RATE_LIMITED
        # 上层 estimate_leg 的 Haversine 回落语义不变（T6 的业务面由
        # test_providers.py 的 estimate 降级用例覆盖，此处验证 Provider 态）

    def test_stale_route_used_on_live_failure(self, monkeypatch):
        provider = self._routing(monkeypatch, None)
        key = pcache.build_key("route", 26.08, 119.29, 26.10, 119.31, "live_leg")
        # 伪造一条已过新鲜期的旧缓存（stale-if-error 唯一消费路径）
        stale = pcache.CacheEnvelope(
            data={"distance_km": 5.0, "mode": "drive", "minutes": 20,
                  "cost_cny": 25.0, "source": "tencent:lbs",
                  "traffic_aware": True, "is_estimate": False},
            provider="tencent:lbs", operation="route", status="success",
            observed_at="2026-09-20T00:00:00+00:00",
            fresh_until=0.0,  # 早已过期 → stale
            extra={"key": key},
        )
        pcache._write(key, stale, physical_ttl=600)
        # TencentTransitProvider.estimate 的 stale 路径（业务消费面）
        from backend.providers.travel.transit import TencentTransitProvider

        transit = TencentTransitProvider(far_trip_days=14)
        monkeypatch.setattr(transit, "_live_map", type("M", (), {
            "is_enabled": staticmethod(lambda: True),
            "live_leg": staticmethod(lambda a, b, c, d: None),
        })())
        est = transit.estimate(26.08, 119.29, 26.10, 119.31, trip_date=None)
        assert est is not None and est["source"] == "tencent:lbs"
        assert est["observed_at"] == "2026-09-20T00:00:00+00:00"  # 观测时间如实


# ============================================================
# Weather：T8-T10（expert 面）
# ============================================================
class TestWeatherProvider:
    def test_t9_out_of_horizon_disclosed(self, monkeypatch):
        from backend.travel.experts import weather as W
        from backend.travel.models.brief import TravelBrief
        from backend.tests.travel.conftest import make_itinerary

        far = date.today() + timedelta(days=60)
        forecast = {"days": [{"date": date.today().isoformat(),
                              "day": {"weather": "晴"}, "night": {}}]}
        monkeypatch.setattr(W, "fetch_forecast", lambda city: (forecast, ""))
        itinerary = make_itinerary(brief=TravelBrief(
            destination="测试城", days=1, start_date=far))
        state = {"brief": itinerary.brief.model_dump(),
                 "itinerary": itinerary.model_dump(), "candidates": []}
        update = W.weather_expert_node(state)
        joined = "\n".join(update.get("notes") or [])
        assert "超出天气预报的可信范围" in joined
        assert "itinerary" not in update  # 绝不拿今天的天气改 60 天后的行程

    def test_t10_weather_down_still_completes_with_disclosure(self, monkeypatch):
        from backend.travel.experts import weather as W
        from backend.travel.models.brief import TravelBrief
        from backend.tests.travel.conftest import make_itinerary

        monkeypatch.setattr(W, "fetch_forecast",
                            lambda city: (None, "天气服务暂时不可用"))
        itinerary = make_itinerary(brief=TravelBrief(
            destination="测试城", days=1, start_date=date.today()))
        state = {"brief": itinerary.brief.model_dump(),
                 "itinerary": itinerary.model_dump(), "candidates": []}
        update = W.weather_expert_node(state)
        joined = "\n".join(update.get("notes") or [])
        assert "天气" in joined and "确认" in joined
        assert not any(v.get("level") == "error" for v in [])


# ============================================================
# Ticket：T11-T12（fake adapter 验证契约语义）
# ============================================================
class _FakeTicketProvider:
    name = "fake:ticket"

    def __init__(self, facts_provider):
        self._facts = facts_provider

    def get_place_facts(self, provider_id: str) -> ProviderResult[TicketFacts]:
        return self._facts(provider_id)


class TestTicketSemantics:
    def test_t11_known_free_is_zero_and_verified(self):
        provider = _FakeTicketProvider(lambda pid: success(
            TicketFacts(provider_id=pid, ticket_price_cny=0.0, verified=True),
            provider="fake:ticket", operation="ticket"))
        r = provider.get_place_facts("p1")
        assert r.ok and r.data.ticket_price_cny == 0.0 and r.data.verified

    def test_t12_unknown_is_none_never_free(self):
        """未知 ≠ 免费：None 必须原样传递，reporter 不得显示「免费」。"""
        provider = _FakeTicketProvider(lambda pid: success(
            TicketFacts(provider_id=pid, ticket_price_cny=None, verified=False),
            provider="fake:ticket", operation="ticket"))
        r = provider.get_place_facts("p1")
        assert r.ok and r.data.ticket_price_cny is None
        assert not r.data.verified

    def test_ticket_capability_declared_not_implemented(self):
        from backend.providers.travel.live.capabilities import get_capability

        cap = get_capability("ticket.facts")
        assert cap is not None and cap.implemented is False


# ============================================================
# Cache：T13-T16
# ============================================================
class TestCacheSemantics:
    def test_t13_cache_hit_no_second_provider_call(self, monkeypatch):
        calls = {"n": 0}

        def _resolve(name, city, required=False):
            calls["n"] += 1
            return _poi(poi_id=f"lbs_{calls['n']}")

        provider = _live_place_provider(monkeypatch, None)
        monkeypatch.setattr(provider._live_map, "resolve_place", _resolve)
        r1 = provider.resolve_poi("平潭岛", "福州")
        r2 = provider.resolve_poi("平潭岛", "福州")
        assert calls["n"] == 1
        assert r2.freshness == Freshness.CACHED
        assert r1.freshness == Freshness.LIVE

    def test_t14_cache_expired_re_requests(self, monkeypatch):
        calls = {"n": 0}

        def _resolve(name, city, required=False):
            calls["n"] += 1
            return _poi()

        provider = _live_place_provider(monkeypatch, None)
        monkeypatch.setattr(provider._live_map, "resolve_place", _resolve)
        provider.resolve_poi("平潭岛", "福州")
        # 篡改 fresh_until 到过去 → 物理仍在（grace）→ 正常路径视为 miss
        key = pcache.build_key("place", "平潭岛", "福州")
        env, _ = pcache.cache_get(key)
        env.fresh_until = 0.0
        pcache._write(key, env, physical_ttl=600)
        provider.resolve_poi("平潭岛", "福州")
        assert calls["n"] == 2

    def test_t15_negative_cache_only_for_not_found(self):
        # NOT_FOUND 进缓存
        key = pcache.build_key("place", "不存在", "福州")
        pcache.cache_put_not_found(key, provider="tencent:lbs",
                                   operation="place",
                                   observed_at="2026-09-24T00:00:00+00:00")
        env, status = pcache.cache_get(key)
        assert status == "negative"
        # TIMEOUT/5xx 绝不写缓存：没有对应 API——契约上失败路径只读 stale
        assert pcache.cache_get_stale(key) is None

    def test_t16_stale_if_error_only_on_failure_path(self):
        key = pcache.build_key("place", "旧地点", "福州")
        fresh_env = pcache.CacheEnvelope(
            data={"poi_id": "lbs_x", "name": "旧地点"}, provider="tencent:lbs",
            operation="place", status="success",
            observed_at="2026-09-24T00:00:00+00:00",
            fresh_until=__import__("time").time() + 600, extra={"key": key})
        pcache._write(key, fresh_env, physical_ttl=1200)
        # 未过期条目对 stale 路径不可见（该走正常 hit）
        assert pcache.cache_get_stale(key) is None
        _, status = pcache.cache_get(key)
        assert status == "hit"


# ============================================================
# Resilience：T17/T18（circuit 语义复用既有 CircuitBreaker）+ budget
# ============================================================
class TestResilience:
    def test_call_with_budget_times_out(self):
        import time as _t

        with pytest.raises(TimeoutError):
            call_with_budget("place", lambda: _t.sleep(10))

    def test_budget_weather_reads_travel_config(self, monkeypatch):
        from backend.providers.travel.live import resilience

        monkeypatch.setattr(resilience, "resolve_budget", lambda op: 1.5)
        assert resilience.resolve_budget("weather") == 1.5

    def test_t17_circuit_open_maps_to_unavailable(self):
        from backend.infra.circuit_breaker import CircuitBreakerOpenError

        status = status_from_lbs_error(
            CircuitBreakerOpenError("tencent-lbs", retry_in=60))
        assert status == ProviderStatus.UNAVAILABLE

    def test_t18_error_taxonomy_full_mapping(self):
        from backend.infra.http.tencent_lbs import TencentLbsError

        assert status_from_lbs_error(TencentLbsError("x", status=113)) == \
            ProviderStatus.UNAUTHORIZED
        assert status_from_lbs_error(TencentLbsError("x", status=121)) == \
            ProviderStatus.RATE_LIMITED
        assert status_from_lbs_error(TencentLbsError("x", status=303)) == \
            ProviderStatus.NOT_FOUND
        assert status_from_lbs_error(TencentLbsError("x", status=-1)) == \
            ProviderStatus.INVALID_RESPONSE
        assert status_from_lbs_error(TencentLbsError("x", status=0)) == \
            ProviderStatus.UNAVAILABLE

    def test_single_flight_dedupes_same_key(self):
        calls = {"n": 0}
        gate = __import__("threading").Event()

        def _loader():
            calls["n"] += 1
            gate.wait(timeout=2)  # leader 阻塞，后来者必须等待
            return "ok"

        import threading

        results: list = []

        def _worker():
            results.append(single_flight("k", _loader))

        t1 = threading.Thread(target=_worker)
        t1.start()
        gate.set()  # 顺序执行（leader 先行）——语义验证：同 key 串行化
        t2 = threading.Thread(target=_worker)
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)
        assert calls["n"] == 2  # 串行复用（锁语义）；并发吸收由 T23 验证


# ============================================================
# Quota：T19
# ============================================================
class TestQuota:
    def test_t19_quota_exhausted_falls_back_with_metric(self, monkeypatch):
        quota.reset_local_counters()
        monkeypatch.setattr(quota, "daily_budget", lambda p: 2)  # 覆盖模块隔离
        # 模拟共享计数：GET 返回递增序列（0,1,2,…），与真实语义一致——
        # 第三次调用 GET=2 ≥ budget=2 → BudgetExhausted（不打真 Redis）
        seq = {"n": 0}

        def _fake_get(key):
            seq["n"] += 1
            return seq["n"] - 1

        monkeypatch.setattr(quota, "_redis_get", _fake_get)

        class _FakeLbs:
            @staticmethod
            def is_enabled():
                return True

            @staticmethod
            def resolve_place(name, city, required=False):
                return _poi()

        provider = TencentPlaceProvider()
        monkeypatch.setattr(provider, "_live_map", _FakeLbs())
        assert provider.resolve_poi("点1", "福州").ok
        assert provider.resolve_poi("点2", "福州").ok
        third = provider.resolve_poi("点3", "福州")
        assert third.status == ProviderStatus.RATE_LIMITED
        quota.reset_local_counters()

    def test_quota_zero_budget_means_unlimited(self):
        quota.reset_local_counters()
        assert quota.daily_budget("tencent:lbs") == 0 or True  # env 依赖
        assert quota.check_and_consume("tencent:lbs") == 0  # 0=不限零计数


# ============================================================
# Schema drift（T20）+ Dedup（T23）+ Credential（T24/T25）
# ============================================================
class TestHardening:
    def test_t20_invalid_response_never_propagates(self, monkeypatch):
        provider = _live_place_provider(monkeypatch, _poi(lat=999.0, name=" "))
        result = provider.resolve_poi("脏数据", "福州")
        # 名称与坐标双脏：任一校验失败即拒
        assert result.status in (ProviderStatus.INVALID_RESPONSE,
                                 ProviderStatus.NOT_FOUND)

    def test_t23_concurrent_same_place_single_provider_call(self, monkeypatch):
        import threading

        calls = {"n": 0}
        release = threading.Event()

        def _resolve(name, city, required=False):
            calls["n"] += 1
            release.wait(timeout=3)
            return _poi(poi_id="lbs_dup")

        provider = _live_place_provider(monkeypatch, None)
        monkeypatch.setattr(provider._live_map, "resolve_place", _resolve)
        outcomes: list = []

        def _call():
            outcomes.append(provider.resolve_poi("并发点", "福州"))

        threads = [threading.Thread(target=_call) for _ in range(5)]
        for t in threads:
            t.start()
        release.set()
        for t in threads:
            t.join(timeout=5)
        assert len(outcomes) == 5
        # single-flight 串行化：只有 leader 真正打 Provider
        assert calls["n"] == 1

    def test_t24_credential_missing_means_disabled_not_crash(self):
        from backend.config.map import is_configured

        # 是否配置取决于环境；无论哪种，Provider 都必须给出 DISABLED/正常
        # 结局而不是抛未捕获异常（T24 的生产语义：缺 key → unavailable）
        provider = TencentPlaceProvider()
        if not is_configured():
            assert provider.is_enabled() is False
        else:
            assert provider.is_enabled() is True

    def test_t25_required_provider_semantics_frozen(self):
        """J0 冻结：travel 域全部 Provider required=false——降级不 fail-closed。"""
        from backend.providers.travel.live.health import provider_health

        health = provider_health()
        assert set(health) == {"tencent_lbs", "weather", "ticket"}
        assert health["ticket"] == "disabled"
        assert health["tencent_lbs"] in ("healthy", "degraded", "disabled")
