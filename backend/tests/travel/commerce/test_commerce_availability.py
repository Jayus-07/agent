"""STOP K Availability Truth 语义矩阵（K4：G5/G8/G15/G10，任务书 §九逐行）。

核心红线（结构性保证 + 测试钉死）：
  Provider TIMEOUT ≠ 没有酒店；Provider ERROR ≠ 航班售罄；
  stale ≠ 实时价；unknown 税 ≠ 含税 0；unknown 库存 ≠ 有房。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

import pytest

from backend.providers.travel.live.fake_commerce import (
    FakeFlightSearchProvider,
    FakeHotelSearchProvider,
    _flights_for,
    _hotels_for,
    seed_stale_cache,
)
from backend.providers.travel.live.result import Freshness, ProviderStatus
from backend.travel.commerce import service as svc
from backend.travel.commerce.models import Availability
from backend.travel.commerce.reporter import render
from backend.travel.commerce.request import (
    FlightSearchRequest,
    HotelSearchRequest,
)


def _hotel_req() -> HotelSearchRequest:
    ci = date.today() + timedelta(days=30)
    return HotelSearchRequest(city="大阪", check_in=ci,
                              check_out=ci + timedelta(days=2))


def _flight_req() -> FlightSearchRequest:
    return FlightSearchRequest(origin="大阪", destination="东京",
                               departure_date=date.today() + timedelta(days=30))


def _patch_hotel(monkeypatch, scenario: dict) -> None:
    monkeypatch.setattr(
        svc, "_resolve_hotel_provider",
        lambda: (FakeHotelSearchProvider(scenario), ""))


def _patch_flight(monkeypatch, scenario: dict) -> None:
    monkeypatch.setattr(
        svc, "_resolve_flight_provider",
        lambda: (FakeFlightSearchProvider(scenario), ""))


# =============================================
# 语义矩阵：成功族
# =============================================


def test_success_offers_semantics(fake_mode):
    r = svc.search_hotels(_hotel_req())
    assert r.status == "success"
    assert all(o.price_snapshot.observed_at for o in r.offers)
    assert all(o.price_snapshot.provider for o in r.offers)
    assert all(o.availability.observed_at for o in r.offers)
    assert all(o.provider == "fake:commerce" for o in r.offers)


def test_empty_result_is_success_semantics_not_failure(fake_mode, monkeypatch):
    """SUCCESS+[] = 「无搜索结果」，结局语义 ≠ 服务失败（§九）。"""
    _patch_hotel(monkeypatch, {"type": "empty"})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "empty" and r.offers == []
    text = render(r)
    assert "没有找到符合条件的酒店" in text
    assert "暂时无法获得" not in text


def test_not_found_negative_cached_and_replays(monkeypatch, fake_mode):
    """NOT_FOUND → negative cache；同键重放命中 negative（零 loader 触达）。"""
    req = _hotel_req()
    provider = FakeHotelSearchProvider({"type": "not_found"})
    r1 = provider.search_hotels(req.city, req.check_in, req.check_out)
    assert r1.status == ProviderStatus.NOT_FOUND
    # 重放：negative cache 命中（error 携带 negative cache 标识，
    # loader/Provider 路径的 error 是「fake no match」）
    r2 = provider.search_hotels(req.city, req.check_in, req.check_out)
    assert r2.status == ProviderStatus.NOT_FOUND
    assert "negative cache" in r2.error
    assert "fake no match" not in r2.error


# =============================================
# 语义矩阵：失败族（绝不映射为售罄/无酒店）
# =============================================


@pytest.mark.parametrize("scenario,expected_status", [
    ("timeout_fast", "timeout"),
    ("unavailable", "unavailable"),
    ("rate_limited", "rate_limited"),
])
def test_failures_produce_zero_offers(fake_mode, monkeypatch, scenario,
                                      expected_status):
    """失败结局不产生任何 offer——「售罄/有房」在结构上不可达（G5）。"""
    _patch_hotel(monkeypatch, {"type": scenario})
    r = svc.search_hotels(_hotel_req())
    assert r.status == expected_status
    assert r.offers == []
    assert r.freshness != Freshness.LIVE or r.offers == []


def test_timeout_is_not_sold_out(fake_mode, monkeypatch):
    _patch_hotel(monkeypatch, {"type": "timeout_fast"})
    text = render(svc.search_hotels(_hotel_req()))
    assert "已订满" not in text
    assert "售罄" not in text
    assert "暂时无法获得" in text
    assert "编造" in text  # 如实披露立场


def test_rate_limited_is_not_not_found(fake_mode, monkeypatch):
    _patch_hotel(monkeypatch, {"type": "rate_limited"})
    text = render(svc.search_hotels(_hotel_req()))
    assert "没有找到" not in text
    assert "稍后再试" in text


def test_unavailable_discloses_honestly(fake_mode, monkeypatch):
    _patch_hotel(monkeypatch, {"type": "unavailable"})
    text = render(svc.search_hotels(_hotel_req()))
    assert "暂时不可用" in text
    assert "已如实停止" in text


# =============================================
# 脏数据 fail-closed（G10）
# =============================================


@pytest.mark.parametrize("scenario", [
    "invalid_price", "invalid_availability", "invalid_price_negative",
])
def test_all_rejected_is_invalid_response_not_empty(fake_mode, monkeypatch,
                                                    scenario):
    """全部记录被拒 = INVALID_RESPONSE（供应商非法响应），不是「无结果」。"""
    _patch_hotel(monkeypatch, {"type": scenario})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "invalid_response"
    assert r.offers == []


def test_flight_invalid_segment_rejected(fake_mode, monkeypatch):
    _patch_flight(monkeypatch, {"type": "invalid_segment"})
    r = svc.search_flights(_flight_req())
    assert r.status == "invalid_response"


def test_flight_invalid_price_rejected(fake_mode, monkeypatch):
    _patch_flight(monkeypatch, {"type": "invalid_price"})
    r = svc.search_flights(_flight_req())
    assert r.status == "invalid_response"


# =============================================
# unknown 语义在结果中的保真（G8）
# =============================================


def test_tax_unknown_rendered_as_unknown_not_zero(fake_mode, monkeypatch):
    _patch_hotel(monkeypatch, {"type": "tax_unknown"})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "success"
    assert r.offers[0].price_snapshot.taxes is None
    text = render(r)
    assert "未知" in text
    assert "含税 0" not in text and "含税0" not in text


def test_sold_out_only_when_provider_declares(fake_mode, monkeypatch):
    """「已订满」只能来自 Provider 明确 sold_out 数据。"""
    _patch_hotel(monkeypatch, {"type": "sold_out"})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "success"
    assert all(o.availability.status == Availability.SOLD_OUT
               for o in r.offers)
    assert "已订满" in render(r)


def test_unknown_availability_never_shown_as_available(fake_mode, monkeypatch):
    _patch_flight(monkeypatch, {"type": "unknown_availability"})
    r = svc.search_flights(_flight_req())
    assert r.status == "success"
    assert all(o.availability.status == Availability.UNKNOWN
               for o in r.offers)
    text = render(r)
    assert "供应商未提供库存信息" in text
    assert "可预订" not in text


def test_currency_preserved_no_implicit_conversion(fake_mode, monkeypatch):
    """H7/F8：日本城市返回 JPY 就显示 JPY，绝不偷偷换 CNY。"""
    _patch_hotel(monkeypatch, {"type": "success"})
    r = svc.search_hotels(_hotel_req())  # 大阪 → JPY（fake 币种表）
    assert r.status == "success"
    assert all(o.price_snapshot.amount.currency == "JPY" for o in r.offers)
    assert "JPY" in render(r)
    assert "≈" not in render(r)  # 无换算符号 = 无隐式换算


# =============================================
# stale-if-error（G7/G16）
# =============================================


def _seed_stale_hotel(req: HotelSearchRequest) -> None:
    rec = _hotels_for(req.city, req.check_in.isoformat(),
                      req.check_out.isoformat(), adults=2, children=0,
                      rooms=1, scenario={"type": "success"})[0]
    key_parts = ["hotel_search", req.city.lower(), req.check_in.isoformat(),
                 req.check_out.isoformat(), 2, 0, 1, 0]
    seed_stale_cache("hotel_search", key_parts[1:], [asdict(rec)])


def test_stale_fallback_marks_stale_not_live(fake_mode, monkeypatch):
    """fresh 过期 → Provider 失败：stale 可用但 freshness 必须如实标注。"""
    _seed_stale_hotel(_hotel_req())
    _patch_hotel(monkeypatch, {"type": "timeout_fast"})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "success"
    assert r.freshness == Freshness.STALE
    assert all(o.price_snapshot.freshness == Freshness.STALE
               for o in r.offers)


def test_stale_render_discloses_non_live(fake_mode, monkeypatch):
    _seed_stale_hotel(_hotel_req())
    _patch_hotel(monkeypatch, {"type": "timeout_fast"})
    text = render(svc.search_hotels(_hotel_req()))
    assert "过期缓存" in text
    assert "非实时" in text
    assert "实时价格：" not in text.split("快照")[0]


def test_flight_stale_fallback(fake_mode, monkeypatch):
    from backend.providers.travel.live.fake_commerce import seed_stale_cache

    req = _flight_req()
    recs = _flights_for("大阪", "东京", req.departure_date.isoformat(),
                        {"type": "success"})
    key_parts = ["flight_search", "大阪", "东京",
                 req.departure_date.isoformat(), "", 1, 0, ""]
    seed_stale_cache("flight_search", key_parts[1:], [asdict(r) for r in recs])
    _patch_flight(monkeypatch, {"type": "timeout_fast"})
    r = svc.search_flights(req)
    assert r.status == "success" and r.freshness == Freshness.STALE
    assert "非实时" in render(r)


# =============================================
# quota 软预算（G22：先查后增）
# =============================================


def test_quota_budget_exhausted_is_rate_limited_not_sold_out(
        fake_mode, monkeypatch):
    from backend.providers.travel.live import quota

    monkeypatch.setattr(quota, "daily_budget", lambda p: 2)
    monkeypatch.setattr(quota, "_redis_incr", lambda key: 3)  # 已超预算
    monkeypatch.setattr(quota, "_redis_get", lambda key: 3)
    _patch_hotel(monkeypatch, {"type": "success"})
    r = svc.search_hotels(_hotel_req())
    assert r.status == "rate_limited"
    assert r.offers == []
    text = render(r)
    assert "已订满" not in text and "售罄" not in text
