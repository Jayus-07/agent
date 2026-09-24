"""STOP K 渲染纪律测试（K6：G7 stale 不伪装 live / §十三禁词 / 缺口披露）。
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.providers.travel.live.fake_commerce import (
    FakeFlightSearchProvider,
    FakeHotelSearchProvider,
)
from backend.travel.commerce import service as svc
from backend.travel.commerce.reporter import FORBIDDEN_PHRASES, render
from backend.travel.commerce.request import (
    FlightSearchRequest,
    HotelSearchRequest,
)


def _hotel_req() -> HotelSearchRequest:
    ci = date.today() + timedelta(days=30)
    return HotelSearchRequest(city="大阪", check_in=ci,
                              check_out=ci + timedelta(days=2))


def _flight_req() -> FlightSearchRequest:
    return FlightSearchRequest(origin="东京", destination="大阪",
                               departure_date=date.today() + timedelta(days=30))


def _run_hotel(monkeypatch, scenario) -> str:
    monkeypatch.setattr(
        svc, "_resolve_hotel_provider",
        lambda: (FakeHotelSearchProvider({"type": scenario}), ""))
    return render(svc.search_hotels(_hotel_req()))


def _run_flight(monkeypatch, scenario) -> str:
    monkeypatch.setattr(
        svc, "_resolve_flight_provider",
        lambda: (FakeFlightSearchProvider({"type": scenario}), ""))
    return render(svc.search_flights(_flight_req()))


def test_forbidden_phrases_never_in_success_render(fake_mode, monkeypatch):
    """§十三禁词清单：任何成功渲染都不得出现预订成功类话术。"""
    text = _run_hotel(monkeypatch, "success")
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase


def test_forbidden_phrases_never_in_flight_render(fake_mode, monkeypatch):
    text = _run_flight(monkeypatch, "success")
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase


def test_success_render_has_snapshot_provenance(fake_mode, monkeypatch):
    """G21：观测时间/来源/新鲜度/链接必须可追踪。"""
    text = _run_hotel(monkeypatch, "success")
    assert "价格观测时间" in text
    assert "来源：fake:commerce" in text
    assert "前往供应商查看实时价格" in text
    assert "搜索时点的价格快照" in text


def test_fake_source_disclosed(fake_mode, monkeypatch):
    """fake 数据源必须显式披露「测试数据」（禁冒充真实报价）。"""
    text = _run_hotel(monkeypatch, "success")
    assert "测试数据（fake:commerce）" in text
    assert "不是真实报价" in text


def test_no_recommendation_claims(fake_mode, monkeypatch):
    """排序依据可解释（按价格），无「最推荐/最佳」类主观断言。"""
    text = _run_hotel(monkeypatch, "success")
    assert "价格从低到高" in text
    assert "最推荐" not in text and "最佳" not in text


def test_deep_link_invalid_rendered_without_link(fake_mode, monkeypatch):
    """链接非法 → deep_link=None：渲染不出现任何链接（而非渲染坏链接）。"""
    text = _run_hotel(monkeypatch, "deeplink_invalid")
    assert "javascript:" not in text
    assert "前往供应商查看" not in text


def test_connection_flight_rendered_with_transfer_info(fake_mode, monkeypatch):
    text = _run_flight(monkeypatch, "connection")
    assert "中转 1 次" in text
    assert " FakeAir" in text
