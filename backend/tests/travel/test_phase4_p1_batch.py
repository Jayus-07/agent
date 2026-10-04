"""tests/travel/test_phase4_p1_batch.py — 四期 P1 批验收测试

覆盖：#64 模糊时间词 / #65 地名歧义口径 / #67 软必去通道（#62 前端墓碑、
#93 前端展示不在本文件）。
"""
from __future__ import annotations

from datetime import date

from backend.travel.agents.requirement_agent import (
    extract_optional_go,
    extract_vague_time_expr,
    extract_fresh_brief,
)
from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief
from backend.travel.services.requirement_service import merge_brief
from backend.tests.travel.conftest import make_poi
from backend.travel.services import poi_service


TODAY = date(2026, 10, 4)


class TestVagueTime:
    def test_month_end_is_vague(self):
        assert extract_vague_time_expr("月底去上海玩两天") == "月底"

    def test_explicit_date_not_vague(self):
        assert extract_vague_time_expr("10月20日去上海") == ""

    def test_relative_date_beats_vague(self):
        """「下周五」有确定日，不再报模糊。"""
        assert extract_vague_time_expr("下周五去上海") == ""

    def test_no_time_word_empty(self):
        assert extract_vague_time_expr("去上海玩两天") == ""


class TestPlaceAmbiguityInvariants:
    def test_alias_table_is_single_valued(self):
        """#65 口径：别名表天然单义（一个别名只映射一个城市），
        目的地歧义在数据层不存在；跨城语义由 #73 多城市说明与
        destination 消歧（否定剔除/最左）承接。"""
        from collections import defaultdict

        from backend.tools.travel import poi_seed
        from backend.travel.data import cities as city_directory

        rev: dict[str, list[str]] = defaultdict(list)
        for alias, city in {**poi_seed.CITY_ALIASES,
                            **city_directory.directory_aliases()}.items():
            rev[alias].append(city)
        dup = {a: cs for a, cs in rev.items() if len(cs) > 1}
        assert not dup, f"别名表出现一对多映射：{dup}"


class TestOptionalGo:
    def test_extract_trigger_capture(self):
        got = extract_optional_go("福州两天，有空再去鼓山")
        assert got == ["鼓山"]

    def test_must_go_not_absorbed_into_optional(self):
        brief = extract_fresh_brief("福州2天必去三坊七巷，有空再去鼓山")
        assert "三坊七巷" in brief.must_go
        assert "三坊七巷" not in brief.optional_go
        assert "鼓山" in brief.optional_go

    def test_avoid_beats_optional(self):
        got = extract_optional_go("有空再去鼓山，别去西禅寺")
        assert "西禅寺" not in got

    def test_merge_union_and_promotion(self):
        prev = TravelBrief(destination="福州", days=2, optional_go=["鼓山"])
        fresh = extract_fresh_brief("必去鼓山")
        merged = merge_brief(prev, fresh)
        # 必去升级后不留在软清单（分级唯一）
        assert "鼓山" in merged.must_go
        assert "鼓山" not in merged.optional_go

    def test_fingerprint_changes_with_optional(self):
        base = TravelBrief(destination="福州", days=2)
        with_opt = base.model_copy(update={"optional_go": ["鼓山"]})
        assert brief_fingerprint(base) != brief_fingerprint(with_opt)


class TestOptionalScheduling:
    def _skeleton(self, brief, candidates):
        return poi_service.build_skeleton(brief, candidates)

    def test_optional_dropped_first_under_capacity(self):
        """容量不足时软必去最先被挤掉（且进 dropped 披露）。"""
        from backend.config import travel as T

        brief = TravelBrief(destination="测试城", days=1, pace="relaxed",
                            optional_go=["软点"])
        candidates = [make_poi(poi_id="m", name="必去景点", required=True),
                      make_poi(poi_id="a", name="普通景点A", rating=4.5),
                      make_poi(poi_id="b", name="普通景点B", rating=4.0),
                      make_poi(poi_id="s", name="软点", rating=4.8)]
        skeleton = self._skeleton(brief, candidates)
        flat = [p.name for day in skeleton.days for p in day]
        assert "必去景点" in flat
        if len(flat) < 4:
            assert "软点" not in flat, "软必去不应挤占普通候选"
            assert "软点" in skeleton.dropped

    def test_repair_drops_optional_first(self):
        """时长超限修复：软必去先于普通候选被移除。"""
        from backend.travel.repair import repair_itinerary
        from backend.travel.validator import check_itinerary
        from backend.tests.travel.conftest import make_day, make_item, make_itinerary

        # 时长超限（PACE_TOO_INTENSE）走「选择删谁」路径：两点各 200 分钟
        # 超 relaxed 上限，删除顺序应先软必去后普通点。开场 09:00 后避开
        # TIME_CLOSED（那是定点删除，测不到排序语义）。
        optional_poi = make_poi(poi_id="s", name="软点", suggested_minutes=200,
                                rating=4.8, open_time="07:00")
        normal_poi = make_poi(poi_id="n", name="普通点", suggested_minutes=200,
                              rating=4.0, open_time="07:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1,
                              optional_go=["软点"]),
            days=[make_day(items=[
                make_item(title="普通点", start="09:00", end="12:20",
                          poi=normal_poi),
                make_item(title="软点", start="12:20", end="15:40",
                          poi=optional_poi),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        names = [i.title for d in repaired.days for i in d.items]
        assert "软点" not in names, "软必去应先于普通点被修复移除"
        assert "普通点" in names


class TestOpenStatus:
    """验收 #77：高德检索时刻营业状态——标注披露+必去警告，不冒充停业。"""

    def _merged(self, monkeypatch, open_status: str):
        import backend.travel.services.poi_service as m

        normal = make_poi(poi_id="tx1", name="鼓山", source="tencent:lbs")
        captured = {}

        def fake_merge_amap(brief, queries, pois, observed_at):
            captured["pois"] = pois
            rec = {"id": "am1", "name": "鼓山", "category": "景点",
                   "lat": 26.05, "lng": 119.39, "rating": 4.7,
                   "open_time_today": "08:00-18:00",
                   "open_status": open_status}
            out = [normal, dict(rec)]
            return out, []

        monkeypatch.setattr(m, "_merge_amap_candidates", fake_merge_amap)
        return m

    def test_closed_status_tagged_and_warned_for_must_go(self, monkeypatch):
        import backend.travel.services.poi_service as m

        monkeypatch.setattr(m.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(m.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
        # 腾讯路 stub 底层检索（真实 _build_live_candidates 跑通合并链路）
        monkeypatch.setattr(
            m.live_search_service, "search_places",
            lambda *, keyword, city, page_size=8: {"pois": [{
                "id": "tx1", "name": "鼓山", "category": "景点",
                "lat": 26.05, "lng": 119.39,
            }]})
        real_merge = m._merge_amap_candidates
        # 直接走真实合并逻辑：stub 掉高德检索，喂 open_status=已打烊
        from backend.travel.services import live_search_service

        class _FakeEnv:
            def search_attractions(self, *, keyword, city, page_size=10):
                return {"merchants": [{
                    "id": "am1", "name": "鼓山", "category": "景点",
                    "lat": 26.05, "lng": 119.39, "rating": 4.7,
                    "open_time_today": "08:00-18:00",
                    "open_status": "已打烊",
                }]}

        monkeypatch.setattr(m.live_search_service, "search_attractions",
                            _FakeEnv().search_attractions)
        brief = TravelBrief(destination="福州", days=2, must_go=["鼓山"],
                            preferences=["人文"])
        candidates, _notes = m.retrieve_candidates(brief)
        target = next(p for p in candidates if p.name == "鼓山")
        assert "高德状态:已打烊" in target.tags
        assert "检索时已打烊" in (target.reason or "")
        skeleton = m.build_skeleton(brief, candidates)
        assert any("已打烊" in n and "鼓山" in n for n in skeleton.notes)

    def test_open_status_not_misread_as_closed(self, monkeypatch):
        import backend.travel.services.poi_service as m

        monkeypatch.setattr(m.T, "TRAVEL_POI_SOURCE", "live")
        monkeypatch.setattr(m.T, "TRAVEL_POI_AMAP_SOURCE_ENABLED", True)
        # 腾讯路 stub 底层检索（真实 _build_live_candidates 跑通合并链路）
        monkeypatch.setattr(
            m.live_search_service, "search_places",
            lambda *, keyword, city, page_size=8: {"pois": [{
                "id": "tx1", "name": "鼓山", "category": "景点",
                "lat": 26.05, "lng": 119.39,
            }]})

        class _FakeEnv:
            def search_attractions(self, *, keyword, city, page_size=10):
                return {"merchants": [{
                    "id": "am1", "name": "鼓山", "category": "景点",
                    "lat": 26.05, "lng": 119.39, "rating": 4.7,
                    "open_time_today": "08:00-18:00",
                    "open_status": "营业中",
                }]}

        monkeypatch.setattr(m.live_search_service, "search_attractions",
                            _FakeEnv().search_attractions)
        brief = TravelBrief(destination="福州", days=2, must_go=["鼓山"],
                            preferences=["人文"])
        candidates, _ = m.retrieve_candidates(brief)
        target = next(p for p in candidates if p.name == "鼓山")
        assert "高德状态:已打烊" not in target.tags
        skeleton = m.build_skeleton(brief, candidates)
        assert not any("已打烊" in n for n in skeleton.notes)
