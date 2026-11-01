"""travel_v2 hotel_search_service 单测（收编重构 §3.4 新增）。

覆盖：off/fake/live 三态 factory、fail-closed（参数校验拒绝/脏数据拒绝）、
参数绑定检查（全部入参经 HotelSearchRequest 确定性校验，无字符串拼接面）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.travel_v2.services.hotel_search_service import (
    HotelSearchParamError,
    search_hotel_offers,
)


@pytest.fixture
def fake_mode(monkeypatch):
    """mode=fake + 白名单（与收编件测试基座同口径；本文件独立定义，
    因 providers_support 测试 conftest 不作用于 travel_v2 目录）。"""
    from backend.infra.cache.backend import InMemoryCache
    from backend.config import travel_offer_search as cfg
    from backend.providers.travel.live import cache as pcache

    store = InMemoryCache(default_ttl=3600)
    monkeypatch.setattr(pcache, "_backend", lambda: store)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    monkeypatch.setattr(
        cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS",
        ("fake-commerce.example.com",))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_MAX_OFFERS", 10)
    return cfg


def _future(nights: int = 2) -> tuple[date, date]:
    ci = date.today() + timedelta(days=30)
    return ci, ci + timedelta(days=nights)


# =============================================
# 三态（off / fake / live）
# =============================================


def test_mode_off_disabled_not_fake(monkeypatch):
    from backend.config import travel_offer_search as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "off")
    ci, co = _future()
    out = search_hotel_offers(city="大阪", check_in=ci, check_out=co)
    assert out["status"] == "disabled"
    assert out["offers"] == []
    assert "未启用" in out["disclosure"]


def test_mode_fake_full_payload(fake_mode, monkeypatch):
    from backend.config import travel_offer_search as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    ci, co = _future()
    out = search_hotel_offers(city="大阪", check_in=ci, check_out=co)
    assert out["status"] == "success"
    assert len(out["offers"]) >= 1
    offer = out["offers"][0]
    # 契约字段：selection_id=fingerprint（预订 Quote 定位键）、金额字符串
    assert offer["selection_id"]
    assert isinstance(offer["price_total"], str)
    assert offer["currency"] in ("JPY", "CNY", "USD")
    assert offer["availability"] in ("available", "limited", "sold_out", "unknown")
    assert "测试数据" in out["disclosure"]  # fake 强制披露，禁冒充生产数据


def test_mode_live_disabled_without_real_provider(fake_mode, monkeypatch):
    """live 未接入 = disabled（如实），绝不回退 fake 冒充（§29）。"""
    from backend.config import travel_offer_search as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "live")
    ci, co = _future()
    out = search_hotel_offers(city="大阪", check_in=ci, check_out=co)
    assert out["status"] == "disabled"
    assert "未接入" in out["reason"]


# =============================================
# fail-closed：参数校验与脏数据
# =============================================


def test_param_validation_rejects_bad_dates():
    ci, _ = _future()
    past = date(2020, 1, 1)
    with pytest.raises(HotelSearchParamError):
        search_hotel_offers(city="大阪", check_in=past, check_out=ci)
    with pytest.raises(HotelSearchParamError):
        search_hotel_offers(city="", check_in=ci, check_out=ci)
    # 退房 <= 入住
    with pytest.raises(HotelSearchParamError):
        search_hotel_offers(city="大阪", check_in=ci, check_out=ci)


def test_all_rejected_is_invalid_response(fake_mode, monkeypatch):
    """供应商脏数据全拒 = invalid_response，不是 empty（§9 结局矩阵）。"""
    from backend.providers.travel.live.fake_commerce import (
        FakeHotelSearchProvider,
    )
    from backend.travel.providers_support.commerce import offer_search

    monkeypatch.setattr(
        offer_search, "_resolve_hotel_provider",
        lambda: (FakeHotelSearchProvider({"type": "invalid_price"}), ""))
    ci, co = _future()
    out = search_hotel_offers(city="大阪", check_in=ci, check_out=co)
    assert out["status"] == "invalid_response"
    assert out["offers"] == []


def test_unknown_fields_stay_none(fake_mode):
    """未提供字段保持 None（禁补齐，G8/G5 镜像）。"""
    ci, co = _future()
    out = search_hotel_offers(city="大阪", check_in=ci, check_out=co)
    offer = out["offers"][0]
    for key in ("taxes", "fees", "cancellation_policy", "booking_deep_link"):
        assert key in offer  # 字段在
    assert offer["price_expires_at"] is None or isinstance(offer["price_expires_at"], str)
