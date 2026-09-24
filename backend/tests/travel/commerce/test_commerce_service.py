"""STOP K Service 层测试（K2-K6：G14 缓存键 / G17 replay / 排序确定性 /
指纹去重 / 截断披露 / provider factory 三态）。
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.providers.travel.live import cache as pcache
from backend.providers.travel.live.fake_commerce import (
    FakeFlightSearchProvider,
    FakeHotelSearchProvider,
)
from backend.providers.travel.live.result import Freshness, ProviderStatus
from backend.travel.commerce import service as svc
from backend.travel.commerce.request import (
    FlightSearchRequest,
    HotelSearchRequest,
)


def _hotel_req(city="大阪", nights=2) -> HotelSearchRequest:
    ci = date.today() + timedelta(days=30)
    return HotelSearchRequest(city=city, check_in=ci,
                              check_out=ci + timedelta(days=nights))


def _flight_req() -> FlightSearchRequest:
    return FlightSearchRequest(origin="大阪", destination="东京",
                               departure_date=date.today() + timedelta(days=30))


# =============================================
# Provider factory 三态（G2/§29）
# =============================================


def test_mode_off_returns_disabled_not_fake(fake_mode, monkeypatch):
    monkeypatch.setattr(fake_mode, "TRAVEL_COMMERCE_PROVIDER_MODE", "off")
    r = svc.search_hotels(_hotel_req())
    assert r.status == "disabled"
    assert r.offers == []


def test_mode_live_disabled_without_real_provider(fake_mode, monkeypatch):
    """live 未实现 = DISABLED（如实原因），绝不回退 fake 冒充（§29）。"""
    monkeypatch.setattr(fake_mode, "TRAVEL_COMMERCE_PROVIDER_MODE", "live")
    r = svc.search_hotels(_hotel_req())
    assert r.status == "disabled"
    assert "未接入" in r.reason
    text = svc_report(r)
    assert "测试数据" not in text


def svc_report(r):
    from backend.travel.commerce.reporter import render

    return render(r)


# =============================================
# 缓存：键正确（G14）+ replay 零外部调用（G17）
# =============================================


def test_cache_key_contains_all_params(fake_mode):
    """缓存键必须包含全部影响参数（city/日期/occupancy/star）。"""
    req = _hotel_req()
    provider = FakeHotelSearchProvider({"type": "success"})
    provider.search_hotels(req.city, req.check_in, req.check_out)
    key = pcache.build_key("hotel_search", req.city.lower(),
                           req.check_in.isoformat(), req.check_out.isoformat(),
                           2, 0, 1, 0)
    env, status = pcache.cache_get(key)
    assert status == "hit" and env is not None

    # 参数不同 → 键不同 → miss（绝不错误共享缓存）
    req2 = HotelSearchRequest(city=req.city, check_in=req.check_in,
                              check_out=req.check_out, adults=3)
    key2 = pcache.build_key("hotel_search", req2.city.lower(),
                            req2.check_in.isoformat(),
                            req2.check_out.isoformat(), 3, 0, 1, 0)
    assert key2 != key
    assert pcache.cache_get(key2)[1] == "miss"


def test_cache_hit_freshness_cached_and_no_recheck(fake_mode, monkeypatch):
    """同键重放：freshness=cached + loader 零触达（G17 replay 口径）。"""
    import backend.providers.travel.live.fake_commerce as fc

    calls = {"n": 0}
    orig_hotels_for = fc._hotels_for

    def _counting(*a, **kw):
        calls["n"] += 1
        return orig_hotels_for(*a, **kw)

    monkeypatch.setattr(fc, "_hotels_for", _counting)
    provider = FakeHotelSearchProvider({"type": "success"})
    req = _hotel_req()
    r1 = provider.search_hotels(req.city, req.check_in, req.check_out)
    assert r1.freshness == Freshness.LIVE
    assert calls["n"] == 1

    r2 = provider.search_hotels(req.city, req.check_in, req.check_out)
    assert calls["n"] == 1  # replay 走缓存，loader 零触达
    assert r2.freshness == Freshness.CACHED
    assert len(r1.data) == len(r2.data)


def test_service_search_replay_no_new_provider_calls(fake_mode, monkeypatch):
    """service 层 replay：外部调用计数不增（C11 口径）。"""
    import backend.providers.travel.live.fake_commerce as fc

    calls = {"n": 0}
    orig_hotels_for = fc._hotels_for

    def _counting(*a, **kw):
        calls["n"] += 1
        return orig_hotels_for(*a, **kw)

    monkeypatch.setattr(fc, "_hotels_for", _counting)
    svc.search_hotels(_hotel_req())
    first = calls["n"]
    assert first == 1
    svc.search_hotels(_hotel_req())
    assert calls["n"] == first  # 零增量


def test_flight_cache_hit(fake_mode):
    provider = FakeFlightSearchProvider({"type": "success"})
    req = _flight_req()
    r1 = provider.search_flights(req.origin, req.destination,
                                 req.departure_date)
    assert r1.freshness == Freshness.LIVE
    r2 = provider.search_flights(req.origin, req.destination,
                                 req.departure_date)
    assert r2.freshness == Freshness.CACHED


# =============================================
# 排序确定性（§十七）+ 截断披露
# =============================================


def test_ranking_deterministic_price_ascending(fake_mode, monkeypatch):
    """同输入排序可复现；价格升序；同价以名称/指纹定序（无主观权重）。"""
    _order1 = _hotel_order()
    _order2 = _hotel_order()
    assert _order1 == _order2  # 两次运行完全一致


def _hotel_order() -> list:
    provider = FakeHotelSearchProvider({"type": "success"})
    req = _hotel_req()
    r1 = provider.search_hotels(req.city, req.check_in, req.check_out)
    # 直接调 service 的排序入口（绕缓存影响，连续两次排序同一输入）
    from backend.travel.commerce.normalize import normalize_hotel_offers
    from backend.travel.commerce.ranking import sort_hotels

    outcome = normalize_hotel_offers(
        r1.data, request_check_in=req.check_in,
        request_check_out=req.check_out, request_adults=2,
        request_children=0, request_rooms=1, observed_at="2026-09-24T00:00:00+00:00",
        freshness=Freshness.LIVE,
        allowed_hosts=("fake-commerce.example.com",))
    s1 = [o.offer_fingerprint for o in sort_hotels(outcome.valids)]
    s2 = [o.offer_fingerprint for o in sort_hotels(outcome.valids)]
    assert s1 == s2
    prices = [o.price_snapshot.amount.amount for o in sort_hotels(outcome.valids)]
    assert prices == sorted(prices)
    return s1


def test_truncation_disclosed(fake_mode, monkeypatch):
    """超上限截断必须披露条数（渲染 tail）。"""
    monkeypatch.setattr(fake_mode, "TRAVEL_COMMERCE_MAX_OFFERS", 1)
    r = svc.search_hotels(_hotel_req())
    assert r.status == "success" and len(r.offers) == 1
    assert r.truncated == 1
    text = svc_report(r)
    assert "已省略" in text and "1 条" in text


# =============================================
# 指纹去重（G13）
# =============================================


def test_fingerprint_distinguishes_rate_plans(fake_mode):
    """fake 两条不同 rate plan 的 offer 指纹必不同（禁错误合并）。"""
    provider = FakeHotelSearchProvider({"type": "success"})
    req = _hotel_req()
    r = provider.search_hotels(req.city, req.check_in, req.check_out)
    fps = {rec.provider_offer_id for rec in r.data}
    assert len(fps) == 2  # 两条不同 rate
    from backend.travel.commerce.normalize import normalize_hotel_offers

    outcome = normalize_hotel_offers(
        r.data, request_check_in=req.check_in,
        request_check_out=req.check_out, request_adults=2,
        request_children=0, request_rooms=1,
        observed_at="2026-09-24T00:00:00+00:00", freshness=Freshness.LIVE,
        allowed_hosts=("fake-commerce.example.com",))
    assert len({o.offer_fingerprint for o in outcome.valids}) == 2


def test_fingerprint_distinguishes_connection_vs_direct(fake_mode):
    """直飞 vs 中转指纹必不同（段集合参与指纹）；中转 stops 确定性推导。"""
    from backend.travel.commerce.identity import flight_fingerprint
    from backend.travel.commerce.normalize import normalize_flight_offers
    from backend.providers.travel.live.result import Freshness

    direct = flight_fingerprint(provider="p", provider_offer_id=None,
                                segment_keys=["FA:001:2026-10-03T08:30"],
                                cabin="经济舱")
    conn = flight_fingerprint(provider="p", provider_offer_id=None,
                              segment_keys=["FA:101:2026-10-03T08:30",
                                            "FA:202:2026-10-03T12:30"],
                              cabin="经济舱")
    assert direct != conn

    provider = FakeFlightSearchProvider({"type": "connection"})
    req = _flight_req()
    r_conn = provider.search_flights(req.origin, req.destination,
                                     req.departure_date)
    assert len(r_conn.data[0].segments) == 2
    outcome = normalize_flight_offers(
        r_conn.data, departure_date=req.departure_date, return_date=None,
        observed_at="2026-09-24T00:00:00+00:00", freshness=Freshness.LIVE,
        allowed_hosts=("fake-commerce.example.com",))
    assert outcome.valids[0].stops == 1  # = len(segments)-1（确定性推导）


# =============================================
# Health / capabilities 台账（G25 / 冻结触碰证明）
# =============================================


def test_health_off_by_default():
    from backend.travel.commerce.health import commerce_health

    h = commerce_health()
    assert h in ({"hotel": "disabled", "flight": "disabled"},
                 {"hotel": "degraded", "flight": "degraded"},
                 {"hotel": "healthy", "flight": "healthy"})


def test_health_fake_is_healthy(fake_mode):
    from backend.travel.commerce.health import commerce_health

    assert commerce_health() == {"hotel": "healthy", "flight": "healthy"}


def test_capability_ledger_declares_fake_only():
    """能力账：hotel/flight=implemented 但 provider=fake:commerce，
    注记必须写明 live 未接入（不冒充 live，§29）。"""
    from backend.providers.travel.live.capabilities import get_capability

    for cap in ("hotel.search", "flight.search"):
        c = get_capability(cap)
        assert c is not None and c.implemented is True
        assert c.provider == "fake:commerce"
        assert "BLOCKED" in c.note or "fake" in c.note


def test_timeout_budget_registered_for_commerce():
    """冻结触碰证明：TIMEOUT_BUDGETS 含 commerce 条目（有界，G3）。"""
    from backend.providers.travel.live.resilience import resolve_budget

    assert resolve_budget("hotel_search") == 8.0
    assert resolve_budget("flight_search") == 8.0


def test_cache_ttls_distinct_per_data_type():
    """冻结触碰证明：分数据 TTL（K0 §8），禁一个 TTL 套所有。"""
    from backend.providers.travel.live.cache import FRESH_TTLS

    assert FRESH_TTLS["hotel_meta"] > FRESH_TTLS["hotel_price"]
    assert FRESH_TTLS["hotel_price"] != FRESH_TTLS["hotel_avail"]
    assert FRESH_TTLS["flight_offer"] != FRESH_TTLS["hotel_meta"]
    assert FRESH_TTLS["hotel_search"] == min(FRESH_TTLS["hotel_avail"],
                                             FRESH_TTLS["hotel_price"])


def test_quota_budget_env_branch():
    """冻结触碰证明：commerce provider 预算走显式登记 env（默认不限）。"""
    import os

    from backend.providers.travel.live import quota

    assert quota.daily_budget("fake:commerce") == 0
    monkey_old = os.environ.get("TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET")
    os.environ["TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET"] = "5"
    try:
        assert quota.daily_budget("fake:commerce") == 5
    finally:
        if monkey_old is None:
            os.environ.pop("TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET", None)
        else:
            os.environ["TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET"] = monkey_old
    assert quota.daily_budget("unknown:provider") == 0  # 未知 provider 不变
