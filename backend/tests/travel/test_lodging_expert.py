# -*- coding: utf-8 -*-
"""收编重构 U2 契约测试：Lodging Expert（travel.hotel.search 链路）。

覆盖（§6 矩阵 · Lodging 行）：三态（fake 全链 / off 如实披露 / live 未接入
disabled）、档位单价确定性换算（CNY 才参与）、booking_status=not_booked、
专家节点 state 契约（lodging_options / expert_history）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.travel.services.lodging_service import (
    lodging_cost_note,
    nightly_price_cny,
    search_lodging,
    tier_for_price,
)


def _dates(nights: int = 2):
    ci = date.today() + timedelta(days=30)
    return ci, ci + timedelta(days=nights)


# =============================================
# 单价/档位确定性换算
# =============================================


def test_nightly_price_deterministic_and_cny_only():
    assert nightly_price_cny("640", "CNY", 2) == 320.0
    assert nightly_price_cny("641", "CNY", 2) == 320.5
    assert nightly_price_cny("640", "JPY", 2) is None   # 禁隐式换算（H7/F8）
    assert nightly_price_cny("x", "CNY", 2) is None
    assert nightly_price_cny("100", "CNY", 0) is None


def test_tier_bands():
    assert tier_for_price(150) == "low"
    assert tier_for_price(388) == "mid"
    assert tier_for_price(900) == "high"
    assert tier_for_price(20_000) is None   # 区间外不硬归档
    assert tier_for_price(None) is None


# =============================================
# 三态（fake / off / live 未接入）
# =============================================


def test_search_lodging_off_disabled(monkeypatch):
    from backend.config import travel_offer_search as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "off")
    ci, co = _dates()
    out = search_lodging(destination="厦门", check_in=ci, check_out=co)
    assert out["status"] == "disabled"
    assert out["options"] == []
    assert "未启用" in out["disclosure"]


def test_search_lodging_fake_full_chain(monkeypatch):
    from backend.infra.cache.backend import InMemoryCache
    from backend.config import travel_offer_search as cfg
    from backend.providers.travel.live import cache as pcache

    monkeypatch.setattr(pcache, "_backend",
                        lambda: InMemoryCache(default_ttl=3600))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS",
                        ("fake-commerce.example.com",))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_MAX_OFFERS", 10)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_DATE_HORIZON_DAYS", 365)

    # fake 大阪返回 JPY—— nightly 为 None 且如实；无 CNY 档位过滤误伤
    ci, co = _dates()
    out = search_lodging(destination="大阪", check_in=ci, check_out=co)
    assert out["status"] == "success"
    assert out["options"], "fake 源应有候选"
    first = out["options"][0]
    assert first["booking_status"] == "not_booked"
    assert first["selection_id"]
    assert "测试数据" in out["disclosure"]


def test_search_lodging_skips_without_dates():
    out = search_lodging(destination="厦门", check_in=None, check_out=None)
    assert out["status"] == "skipped"
    assert out["options"] == []


def test_cost_note_honest(monkeypatch):
    assert "未含住宿项" in (lodging_cost_note({"status": "disabled"}) or "")
    assert lodging_cost_note({"status": "success", "options": [
        {"nightly_price_cny": 388}]}) is None


# =============================================
# 专家节点 state 契约
# =============================================


def _state(city="厦门", with_dates=True):
    from backend.travel.models.brief import TravelBrief

    kwargs: dict = {
        "destination": city,
        "days": 2,
        "party_size": 2,
        "tier": "economy",
    }
    if with_dates:
        kwargs["start_date"] = date.today() + timedelta(days=30)
    return {"brief": TravelBrief(**kwargs).model_dump(mode="json"),
            "user_message": "x", "notes": []}


def test_lodging_expert_node_writes_options_and_history(monkeypatch):
    from backend.travel.experts.lodging import lodging_expert_node

    from backend.infra.cache.backend import InMemoryCache
    from backend.config import travel_offer_search as cfg
    from backend.providers.travel.live import cache as pcache

    monkeypatch.setattr(pcache, "_backend",
                        lambda: InMemoryCache(default_ttl=3600))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "fake")
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS",
                        ("fake-commerce.example.com",))
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_MAX_OFFERS", 10)
    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_DATE_HORIZON_DAYS", 365)

    update = lodging_expert_node(_state("大阪"))
    assert update["expert_history"][-1]["expert"] == "lodging"
    assert update["expert_history"][-1]["status"] == "success"
    options = update.get("lodging_options") or []
    assert options
    assert all(o["booking_status"] == "not_booked" for o in options)
    assert update.get("lodging_disclosure")
    assert any("住宿候选" in n for n in update["notes"])


def test_lodging_expert_off_state_discloses(monkeypatch):
    from backend.travel.experts.lodging import lodging_expert_node

    from backend.config import travel_offer_search as cfg

    monkeypatch.setattr(cfg, "TRAVEL_COMMERCE_PROVIDER_MODE", "off")
    update = lodging_expert_node(_state("厦门"))
    assert update.get("lodging_options") == []
    assert any("未启用" in n for n in update["notes"])


def test_lodging_expert_without_brief_is_noop():
    from backend.travel.experts.lodging import lodging_expert_node

    update = lodging_expert_node({"user_message": "x", "notes": []})
    assert update["expert_history"][-1]["expert"] == "lodging"


# =============================================
# F 住宿档位（P5 验收补缺）：reporter 渲染住宿建议段
# =============================================


def test_reporter_renders_lodging_suggestions_section():
    """lodging_options 进行程单正文（原型「住宿建议：×× ¥388/晚」语义）。"""
    from backend.travel.reporter import _render_lodging_picks

    state = {
        "lodging_options": [
            {"selection_id": "ho:1", "name": "海景酒店", "tier": "mid",
             "nightly_price_cny": 388.0, "availability": "available",
             "address": "环岛路 1 号",
             "deep_link": "https://rollinggo.cn/pages/hotel/detail/index?id=1"},
            {"selection_id": "ho:2", "name": "青旅", "tier": "low",
             "nightly_price_cny": 120.0, "availability": "limited",
             "address": None, "deep_link": None},
        ],
        "lodging_disclosure": "测试数据披露",
    }
    lines = _render_lodging_picks(state)
    text = "\n".join(lines)
    assert "## 住宿建议" in text
    assert "海景酒店" in text and "¥388/晚" in text and "舒适档" in text
    assert "青旅" in text and "¥120/晚" in text and "经济档" in text and "房量紧张" in text
    # 跳转型预订：有 deep_link 的候选渲染「去预订」链接（白名单门后的 URL 原样透传）
    assert "[去预订](https://rollinggo.cn/pages/hotel/detail/index?id=1)" in text
    assert "平台不代订" in text  # 跳转型预订如实披露


def test_reporter_lodging_section_absent_without_options():
    from backend.travel.reporter import _render_lodging_picks

    assert _render_lodging_picks({}) == []
    assert _render_lodging_picks({"lodging_options": []}) == []
