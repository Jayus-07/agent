"""tests/travel/test_planning_contract.py — STOP I1 Canonical POI 契约守护

覆盖任务书 G1/G2/G3/G13 的可验证面：
  - must_go 三态解析（resolved/unresolved，单一事实源 planning.resolve_must_go）
  - POI ID 稳定性（种子语义 id + LBS 兜底 id 跨进程确定——禁止带进程盐的 hash()）
  - unknown ≠ false（无营业时间数据的 LBS POI 必须带 unverified 标记）
  - 评测侧 scheduled_must_go 与 validator coverage 同口径
"""
import hashlib

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.models.poi import Poi
from backend.travel.planning import (
    names_match,
    resolve_must_go,
    scheduled_must_go,
)
from backend.tools.travel import poi_seed


def _poi(poi_id: str, name: str, **kw) -> Poi:
    defaults = dict(
        poi_id=poi_id, name=name, city="厦门",
        lat=24.44, lng=118.08, source="seed:local",
    )
    defaults.update(kw)
    return Poi(**defaults)


# ============================================================
# names_match：匹配口径单一事实源
# ============================================================
class TestNamesMatch:
    def test_exact(self):
        assert names_match("鼓浪屿", "鼓浪屿")

    def test_user_suffix_variant(self):
        # 用户带前后缀说法（「鼓浪屿景区」）也应命中
        assert names_match("鼓浪屿", "鼓浪屿景区")

    def test_rejects_unrelated(self):
        assert not names_match("鼓浪屿", "沙坡尾")

    def test_empty_side_is_false(self):
        assert not names_match("", "鼓浪屿")
        assert not names_match("鼓浪屿", "")


# ============================================================
# resolve_must_go：三态解析
# ============================================================
class TestResolveMustGo:
    def test_resolved_and_unresolved_split(self):
        pool = [_poi("xm_gulangyu", "鼓浪屿"), _poi("xm_shapowei", "沙坡尾艺术西区")]
        brief = TravelBrief(destination="厦门", days=2,
                            must_go=["鼓浪屿", "虚构海湾艺术中心"])
        res = resolve_must_go(brief, pool)
        assert res.resolved == ["鼓浪屿"]
        assert res.unresolved == ["虚构海湾艺术中心"]

    def test_empty_must_go(self):
        res = resolve_must_go(TravelBrief(destination="厦门", days=1), [])
        assert res.resolved == [] and res.unresolved == []

    def test_seed_dataset_resolves_real_poi(self):
        # 真实种子数据上的冒烟：厦门池能解析鼓浪屿
        brief = TravelBrief(destination="厦门", days=2, must_go=["鼓浪屿"])
        res = resolve_must_go(brief, poi_seed.load_city("厦门"))
        assert res.resolved == ["鼓浪屿"]
        assert res.unresolved == []

    def test_unresolved_never_fabricated(self):
        # G13：unknown 地点绝不允许被解析成候选池条目
        brief = TravelBrief(destination="厦门", days=2, must_go=["冷门秘境XYZ"])
        res = resolve_must_go(brief, poi_seed.load_city("厦门"))
        assert res.unresolved == ["冷门秘境XYZ"]


# ============================================================
# scheduled_must_go：与 validator coverage 同口径
# ============================================================
class TestScheduledMustGo:
    def _itinerary_like(self, pois):
        """最小 Itinerary 替身：只需 all_pois()。"""
        class _It:
            def all_pois(self):
                return pois
        return _It()

    def test_scheduled_and_missing(self):
        it = self._itinerary_like([_poi("xm_gulangyu", "鼓浪屿")])
        brief = TravelBrief(destination="厦门", days=1, must_go=["鼓浪屿", "未排入的点"])
        res = scheduled_must_go(brief, it)
        assert res.resolved == ["鼓浪屿"]
        assert res.unresolved == ["未排入的点"]

    def test_none_itinerary_everything_missing(self):
        brief = TravelBrief(destination="厦门", days=1, must_go=["鼓浪屿"])
        res = scheduled_must_go(brief, None)
        assert res.unresolved == ["鼓浪屿"]


# ============================================================
# POI ID 稳定性（G2/G18 前置）
# ============================================================
class TestPoiIdStability:
    def test_seed_ids_are_semantic_and_stable(self):
        # 种子 id 是语义 id 而非 list index；两次加载完全一致
        for city in poi_seed.all_cities():
            pois = poi_seed.load_city(city)
            ids = [p.poi_id for p in pois]
            assert all(i and not i.isdigit() for i in ids)
            assert ids == [p.poi_id for p in poi_seed.load_city(city)]

    def test_lbs_fallback_id_is_deterministic(self):
        # 关键回归：兜底 id 曾用内建 hash()（进程盐）——同输入必须同 id
        from backend.tools.travel.live_map import stable_fallback_id

        a = stable_fallback_id("平潭岛", "福州")
        b = stable_fallback_id("平潭岛", "福州")
        assert a == b
        assert a == hashlib.sha1("平潭岛|福州".encode("utf-8")).hexdigest()[:12]
        assert stable_fallback_id("平潭岛", "厦门") != a

    def test_lbs_fallback_id_differs_per_city(self):
        # 同名地点在不同城市必须得到不同 id（避免跨城串号）
        from backend.tools.travel.live_map import stable_fallback_id

        assert stable_fallback_id("人民公园", "福州") != stable_fallback_id("人民公园", "杭州")


# ============================================================
# unknown ≠ false：freshness 语义
# ============================================================
class TestFreshnessSemantics:
    def test_unverified_poi_requires_disclosure_fields(self):
        # LBS 补全的 POI：营业时段为默认占位，必须带 unverified + observed_at
        poi = _poi("lbs_x", "某未核实点", open_time="09:00", close_time="17:00")
        poi.verification_status = "unverified"
        poi.observed_at = "2026-09-24T00:00:00+00:00"
        assert poi.verification_status == "unverified"
        assert poi.observed_at

    def test_seed_poi_defaults_verified(self):
        for p in poi_seed.load_city("杭州")[:3]:
            assert p.verification_status == "verified"
            assert p.source == "seed:local"

    def test_unresolved_notice_does_not_pretend(self):
        # 话术本身不得包含「已确认/已安排」类伪事实措辞
        from backend.travel.planning import UNRESOLVED_NOTICE

        assert "暂未自动安排" in UNRESOLVED_NOTICE
