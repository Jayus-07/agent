"""test_live_poi_source.py — TRAVEL_POI_SOURCE=live 候选池实时源（离线单测）

纯 stub live_search_service.search_places，不打真实 LBS；覆盖：
构造/去重/诚实字段、失败披露（严格模式）、显式种子回退。
"""
from __future__ import annotations

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.services import live_search_service, poi_service


def _brief(**kw) -> TravelBrief:
    params = {"destination": "杭州", "days": 2, "party_size": 2}
    params.update(kw)
    return TravelBrief(**params)


def _lbs_item(pid: str, name: str, lat = 30.25, lng = 120.15,
              category: str = "景点:博物馆") -> dict:
    return {"id": pid, "name": name, "lat": lat, "lng": lng,
            "category": category, "address": "测试地址"}


def test_live_candidates_build_and_dedup(monkeypatch):
    """同 id 去重 + 诚实字段（tencent:lbs / unverified / 占位时长与门票）。"""
    data = {"pois": [
        _lbs_item("1", "西湖博物馆"),
        _lbs_item("1", "西湖博物馆"),  # 同 id 重复 → 去重
        _lbs_item("2", "断桥", lat=30.26, lng=120.16, category="景点:风景"),
    ]}
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: data)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, notes = poi_service.retrieve_candidates(_brief(preferences=["人文"]))
    assert len(candidates) == 2
    assert all(p.poi_id.startswith("lbs:") for p in candidates)
    poi = candidates[0]
    assert poi.name == "西湖博物馆"
    assert poi.source == "tencent:lbs"
    assert poi.observed_at is not None
    assert poi.verification_status == "unverified"
    assert poi.suggested_minutes == 120
    assert poi.ticket_cny == 0.0
    assert notes == []


def test_live_total_failure_disclosed_no_fallback(monkeypatch):
    """严格模式（默认）：LBS 全失败 → 空候选 + 如实披露，不回退种子。"""
    def boom(**kw):
        raise live_search_service.LiveSearchError("配额尽")

    monkeypatch.setattr(live_search_service, "search_places", boom)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_FALLBACK_SEED", False)

    candidates, notes = poi_service.retrieve_candidates(_brief())
    assert candidates == []
    assert any("无结果" in n for n in notes)


def test_live_failure_with_explicit_seed_fallback(monkeypatch):
    """显式开启回退：live 失败 → 种子兜底 + 留痕「非实时」。"""
    from backend.travel.models.poi import Poi

    def boom(**kw):
        raise live_search_service.LiveSearchError("上游不可用")

    seed_poi = Poi(poi_id="seed:hz-1", name="种子景点", city="杭州", lat=30.25, lng=120.15)
    monkeypatch.setattr(live_search_service, "search_places", boom)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_FALLBACK_SEED", True)
    monkeypatch.setattr(poi_service, "_seed_candidates", lambda brief: [seed_poi])

    candidates, notes = poi_service.retrieve_candidates(_brief())
    assert [p.poi_id for p in candidates] == ["seed:hz-1"]
    assert any("回退本地种子" in n for n in notes)


def test_seed_channel_untouched(monkeypatch):
    """TRAVEL_POI_SOURCE=seed：走原 search_poi 通道，行为不变。"""
    from backend.travel.models.poi import Poi

    seed_poi = Poi(poi_id="seed:fz-1", name="三坊七巷", city="福州", lat=26.08, lng=119.30)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "seed")
    monkeypatch.setattr(poi_service, "_seed_candidates", lambda brief: [seed_poi])

    candidates, notes = poi_service.retrieve_candidates(_brief(destination="福州"))
    assert [p.poi_id for p in candidates] == ["seed:fz-1"]
    assert notes == []
