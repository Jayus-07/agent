"""test_live_poi_source.py — TRAVEL_POI_SOURCE=live 候选池实时源（离线单测）

纯 stub live_search_service.search_places，不打真实 LBS；覆盖：
构造/去重/诚实字段、失败披露（严格模式）、显式种子回退。
"""
from __future__ import annotations

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
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


# ── A1 评分源：高德景点类目并入候选池（2026-10-04）──────────────

def _amap_merchant(name: str, rating: float = 4.7, lat: float = 30.25,
                   lng: float = 120.15,
                   open_time: str = "08:00-17:30") -> dict:
    return {"id": f"am-{name}", "name": name, "category": "景点:风景名胜",
            "rating": rating, "avg_cost_cny": None,
            "open_time_today": open_time, "open_time_week": "",
            "tel": "", "lat": lat, "lng": lng, "distance_m": None,
            "source": "amap", "updated_at": "2026-10-04T12:00:00+08:00"}


def _enable_amap(monkeypatch, merchants_by_query: dict[str, list[dict]]):
    """开启 A1 源并 stub 高德检索：query → merchants。"""
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
    monkeypatch.setattr(
        live_search_service, "search_attractions",
        lambda *, keyword, city, page_size=10: {
            "keyword": keyword, "count": len(merchants_by_query.get(keyword, [])),
            "merchants": merchants_by_query.get(keyword, [])})


def test_amap_rating_merged_and_dedup(monkeypatch):
    """同名处用高德版替换（rating/营业时间进 Poi）+ 独有地点追加。"""
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": [
        _lbs_item("1", "西湖风景区"),  # 与高德「西湖」包含+坐标近 → 合并
        _lbs_item("2", "断桥", lat=30.26, lng=120.16),
    ]})
    _enable_amap(monkeypatch, {"公园 风景名胜": [
        _amap_merchant("西湖", rating=4.7),  # 近坐标（30.25,120.15）
        _amap_merchant("灵隐寺", rating=4.8, lat=30.24, lng=120.10),
    ]})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, notes = poi_service.retrieve_candidates(_brief(preferences=["自然"]))
    assert notes == []
    by_name = {p.name: p for p in candidates}
    # 合并：高德版替换腾讯版（评分/营业时间/来源全带）
    assert by_name["西湖"].rating == 4.7
    assert by_name["西湖"].source == "amap"
    assert by_name["西湖"].open_time == "08:00"
    assert by_name["西湖"].close_time == "17:30"
    assert by_name["西湖"].poi_id.startswith("amap:")
    # 高德独有地点追加，同样带评分
    assert by_name["灵隐寺"].rating == 4.8
    # 未合并的腾讯条目保持原样
    assert by_name["断桥"].source == "tencent:lbs"
    assert by_name["断桥"].rating == 0.0


def test_amap_no_false_merge_distant_same_prefix(monkeypatch):
    """名字包含但相距远（「西湖」vs「西湖博物馆」）不误合并，两条并存。"""
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": [
        _lbs_item("1", "西湖博物馆", lat=30.26, lng=120.20),
    ]})
    _enable_amap(monkeypatch, {"公园 风景名胜": [
        _amap_merchant("西湖", lat=30.13, lng=120.13),  # 相距数公里
    ]})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, _ = poi_service.retrieve_candidates(_brief(preferences=["自然"]))
    names = {p.name for p in candidates}
    assert names == {"西湖博物馆", "西湖"}
    museum = next(p for p in candidates if p.name == "西湖博物馆")
    assert museum.source == "tencent:lbs"  # 保留腾讯版，不被误替换


def test_amap_failure_disclosed_tencent_intact(monkeypatch):
    """高德单源失败：腾讯候选照常（评分缺失留痕披露，不空、不炸）。"""
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": [
        _lbs_item("1", "断桥"),
    ]})

    def boom(*, keyword, city, page_size=10):
        raise live_search_service.LiveSearchError("配额尽")

    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
    monkeypatch.setattr(live_search_service, "search_attractions", boom)
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, notes = poi_service.retrieve_candidates(_brief(preferences=["自然"]))
    assert [p.name for p in candidates] == ["断桥"]
    assert any("高德评分源检索失败" in n for n in notes)


def test_amap_disabled_keeps_legacy_behavior(monkeypatch):
    """开关关闭：行为与旧版完全一致（不发高德请求、无评分）。"""
    calls: list[str] = []
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": [
        _lbs_item("1", "断桥"),
    ]})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", False)
    monkeypatch.setattr(
        live_search_service, "search_attractions",
        lambda *, keyword, city, page_size=10: calls.append(keyword) or {})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    candidates, notes = poi_service.retrieve_candidates(_brief(preferences=["自然"]))
    assert calls == []
    assert notes == []
    assert candidates[0].source == "tencent:lbs"


def test_amap_open_hours_parse():
    """营业时段串解析：标准段取第一段；杂串/空返回 None（占位默认）。"""
    assert poi_service._amap_open_hours("08:00-17:30") == ("08:00", "17:30")
    assert poi_service._amap_open_hours("09:00-14:00,17:00-22:00") == ("09:00", "14:00")
    assert poi_service._amap_open_hours("营业时间：9:00-17:00") == ("09:00", "17:00")
    assert poi_service._amap_open_hours("") is None
    assert poi_service._amap_open_hours("全天开放") is None


def test_amap_rating_orders_skeleton(monkeypatch):
    """评分进 Poi 后骨架排序生效：rating 降序（此前全 0 退化为 id 序）。"""
    monkeypatch.setattr(live_search_service, "search_places", lambda **kw: {"pois": []})
    _enable_amap(monkeypatch, {"公园 风景名胜": [
        _amap_merchant("普通景点", rating=3.9, lat=30.25, lng=120.15),
        _amap_merchant("高分景点", rating=4.9, lat=30.26, lng=120.16),
        _amap_merchant("低分景点", rating=3.2, lat=30.27, lng=120.17),
    ]})
    monkeypatch.setattr(poi_service.T, "TRAVEL_POI_SOURCE", "live")

    brief = _brief(days=1, preferences=["自然"])
    candidates, _ = poi_service.retrieve_candidates(brief)
    skeleton = poi_service.build_skeleton(brief, candidates)
    scheduled = [p.name for day in skeleton.days for p in day]
    assert scheduled == ["高分景点", "普通景点", "低分景点"]


# ── A4 美食三要素排序（2026-10-04）────────────────────────────

def _merchant(name: str, rating: float | None, lat: float, lng: float,
              category: str = "餐饮服务:中餐厅") -> dict:
    return {"id": f"m-{name}", "name": name, "category": category,
            "rating": rating, "price": None, "avg_cost_cny": None,
            "address": "", "open_status": "未知", "open_time_today": "",
            "open_time_week": "", "tel": "", "lat": lat, "lng": lng,
            "distance_m": None, "source": "amap",
            "updated_at": "2026-10-04T12:00:00+08:00"}


def _poi(name: str, lat: float, lng: float) -> Poi:
    return Poi(poi_id=f"lbs:{name}", name=name, city="福州",
               lat=lat, lng=lng)


def test_food_rank_near_and_high_rating_first():
    """评分高+离骨架质心近的排最前；distance_m 注入每项。"""
    food = {"keyword": "美食", "count": 3, "merchants": [
        _merchant("远而平", 4.8, 26.10, 119.40),   # 评分高但 ~11km
        _merchant("近而高", 4.6, 26.06, 119.30),   # 质心旁 ~1km
        _merchant("近而无分", None, 26.05, 119.30),
    ]}
    ranked = poi_service.rank_food_merchants(
        food, [_poi("景点A", 26.05, 119.30), _poi("景点B", 26.06, 119.31)])
    names = [m["name"] for m in ranked["merchants"]]
    assert names[0] == "近而高"  # 近+高分综合最优
    assert all(m["distance_m"] is not None for m in ranked["merchants"])


def test_food_rank_no_rating_neutral_not_zero():
    """无评分按中性 2.5 计（不给 0 冤枉沉底），仍参与距离排序。"""
    food = {"merchants": [
        _merchant("无分但近", None, 26.05, 119.30),
        _merchant("有分但远", 4.0, 26.15, 119.45),
    ]}
    ranked = poi_service.rank_food_merchants(
        food, [_poi("景点", 26.05, 119.30)])
    assert ranked["merchants"][0]["name"] == "无分但近"


def test_food_rank_empty_skeleton_keeps_order():
    """空骨架（无距离参照）保持原序，不硬排。"""
    food = {"merchants": [_merchant("甲", 4.0, 26.0, 119.0),
                          _merchant("乙", 4.5, 26.1, 119.1)]}
    ranked = poi_service.rank_food_merchants(food, [])
    assert [m["name"] for m in ranked["merchants"]] == ["甲", "乙"]


def test_food_rank_category_boost_applied(monkeypatch):
    """品类加权表启用时生效（配置驱动，默认空表不加分）。"""
    from backend.travel.services import poi_service as ps

    food = {"merchants": [
        _merchant("普通店", 4.5, 26.05, 119.30, "餐饮服务:中餐厅:川菜"),
        _merchant("闽菜馆", 4.5, 26.05, 119.30, "餐饮服务:中餐厅:福建菜"),
    ]}
    ranked = ps.rank_food_merchants(food, [_poi("景点", 26.05, 119.30)])
    assert ranked["merchants"][0]["name"] in {"普通店", "闽菜馆"}  # 默认无加权，稳定序即可

    monkeypatch.setattr(ps.T, "TRAVEL_FOOD_CATEGORY_BOOSTS", {"福建菜": 0.6})
    ranked = ps.rank_food_merchants(food, [_poi("景点", 26.05, 119.30)])
    assert ranked["merchants"][0]["name"] == "闽菜馆"


# ── A3 本地攻略源：文档名解析景点 → 补坐标入池（2026-10-04）──────

def test_local_doc_names_parse(monkeypatch):
    """文档名解析：只取「{目的地}-景点-{名}.md」，城市档/美食档/他城排除。"""
    class _FakeRegistry:
        def list_by_kb(self, kb_id):
            assert kb_id == "travel"
            return [
                {"file_name": "福州-景点-于山.md"},
                {"file_name": "福州-景点-鼓山.md"},
                {"file_name": "福州-城市-福州市.md"},   # 城市档排除
                {"file_name": "福州-美食-佛跳墙.md"},   # 美食档排除
                {"file_name": "厦门-景点-鼓浪屿.md"},   # 他城排除
                {"file_name": "福州旅游攻略.md"},        # 非规范名排除
            ]

    import backend.travel.services.poi_service as ps
    monkeypatch.setattr(
        "backend.rag.indexing.doc_registry_pg.PostgresDocumentRegistry",
        lambda: _FakeRegistry())
    names = ps._local_doc_attraction_names("福州")
    assert names == ["于山", "鼓山"]


def test_local_doc_names_registry_failure_soft(monkeypatch):
    """registry 不可用 → 空列表软失败，不炸检索。"""
    def boom():
        raise RuntimeError("pg down")
    import backend.travel.services.poi_service as ps
    monkeypatch.setattr(
        "backend.rag.indexing.doc_registry_pg.PostgresDocumentRegistry", boom)
    assert ps._local_doc_attraction_names("福州") == []


def test_local_doc_merge_enriches_small_pool(monkeypatch):
    """池子<阈值时本地景点经腾讯补坐标入池，reason=本地攻略收录。"""
    import backend.travel.services.poi_service as ps

    monkeypatch.setattr(ps.T, "TRAVEL_POI_LOCAL_DOC_ENABLED", True)
    monkeypatch.setattr(ps.T, "TRAVEL_POI_LOCAL_DOC_MIN_POOL", 15)
    monkeypatch.setattr(ps, "_local_doc_attraction_names", lambda dest: ["于山", "鼓山"])
    added = [_poi("于山", 26.08, 119.30)]
    monkeypatch.setattr(
        "backend.providers.travel.live.tencent.resolve_missing_places",
        lambda city, candidates, wanted: (added, ["provider notes"]))

    pois = [_lbs_poi := Poi(poi_id="lbs:1", name="西湖", city="福州",
                            lat=26.05, lng=119.30)]
    merged, notes = ps._merge_local_doc_candidates(_brief_local(), pois)
    assert any(p.name == "于山" and p.reason == "本地攻略收录" for p in merged)
    assert any("本地攻略补入 1 个地点" in n for n in notes)


def test_local_doc_skipped_when_pool_large(monkeypatch):
    """池子≥阈值：本地源不启用（省逐名补坐标的开销）。"""
    import backend.travel.services.poi_service as ps

    monkeypatch.setattr(ps.T, "TRAVEL_POI_LOCAL_DOC_ENABLED", True)
    monkeypatch.setattr(ps.T, "TRAVEL_POI_LOCAL_DOC_MIN_POOL", 3)
    called = []
    monkeypatch.setattr(ps, "_local_doc_attraction_names",
                        lambda dest: called.append(dest) or ["于山"])
    big_pool = [Poi(poi_id=f"lbs:{i}", name=f"景点{i}", city="福州",
                    lat=26.0 + i * 0.01, lng=119.0) for i in range(3)]
    merged, notes = ps._merge_local_doc_candidates(_brief_local(), big_pool)
    assert called == []
    assert merged == big_pool and notes == []


def _brief_local(**kw):
    params = {"destination": "福州", "days": 2, "party_size": 2}
    params.update(kw)
    from backend.travel.models.brief import TravelBrief
    return TravelBrief(**params)
