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


# ── 2026-10-03：「行程全是吃的」根治 + 入选理由 ─────────────────────

def test_food_preference_keeps_sightseeing_queries(monkeypatch):
    """美食偏好不再把候选池检索词锁死为「美食」（此前 LBS 返回全是餐厅，
    行程被挤成全是吃的）：无景点类偏好时回落兜底景点词。"""
    seen: list[str] = []

    def fake_search(**kw):
        seen.append(kw["keyword"])
        return {"pois": [_lbs_item("1", "西湖", category="景点:风景")]}

    monkeypatch.setattr(live_search_service, "search_places", fake_search)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, _ = poi_service.retrieve_candidates(_brief(preferences=["美食"]))
    assert seen == list(poi_service._LIVE_DEFAULT_QUERIES)
    assert all(p.category != "美食" for p in candidates)
    # 候选带基础入选理由（「为什么选它」的检索事实层）
    assert candidates[0].reason == "「风景名胜」实时检索"


def test_meal_candidates_not_scheduled_but_disclosed(monkeypatch):
    """骨架类别策略：餐饮候选不排入行程且如实披露；点名必去的餐厅保留。"""
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": [
        _lbs_item("1", "老字号餐厅", category="餐饮:中餐厅"),
        _lbs_item("2", "西湖", category="景点:风景"),
        _lbs_item("3", "必去餐厅", category="餐饮:小吃"),
        _lbs_item("4", "达明美食街", category="美食:美食街"),
    ]})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    brief = _brief(days=1, must_go=["必去餐厅"])
    candidates, _ = poi_service.retrieve_candidates(brief)
    skeleton = poi_service.build_skeleton(brief, candidates)

    scheduled = [p.name for day in skeleton.days for p in day]
    assert "老字号餐厅" not in scheduled
    assert "西湖" in scheduled
    assert "必去餐厅" in scheduled
    # 游玩型餐饮区（美食街）不是「坐下吃饭的店」，保留排入
    assert "达明美食街" in scheduled
    assert any("餐饮类候选不排进行程" in n and "老字号餐厅" in n for n in skeleton.notes)
