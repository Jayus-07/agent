"""tests/travel/test_pool_contract.py — STOP I4 候选池成员与全行程结构守护

覆盖任务书 T11/T12 与 G3/G7 的可验证面：
  - 非法 poi_id 注入行程 → validator 必须 error（POI_NOT_IN_CANDIDATES，T11）
  - 无候选池事实时该轴 not_evaluable（不假通过不假失败）
  - 跨天重复安排 → error（POI_DUPLICATED）；同天重复保持既有 GEO_REVISIT（T12）
  - repair 对两类违反的真实修复（有界、必去重复项摘后续留首现）
  - transit 专家对 day_plan 里候选池外 id 的显式留痕（不再静默蒸发）
"""
from __future__ import annotations

from backend.tests.travel.conftest import (
    make_day,
    make_item,
    make_itinerary,
    make_poi,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.validation import (
    CODE_GEO_REVISIT,
    CODE_POI_DUPLICATED,
    CODE_POI_NOT_IN_CANDIDATES,
    LEVEL_ERROR,
)
from backend.travel.repair import repair_itinerary
from backend.travel.validator import check_itinerary


def _codes(report) -> list[str]:
    return report.codes()


# ============================================================
# 候选池成员轴（T11）
# ============================================================
class TestPoolAxis:
    def test_unknown_poi_id_is_error(self):
        outsider = make_poi(poi_id="ghost", name="虚构海湾艺术中心")
        day = make_day(items=[make_item(poi=outsider)])
        report = check_itinerary(make_itinerary(days=[day]),
                                 valid_poi_ids={"p1", "p2"})
        assert CODE_POI_NOT_IN_CANDIDATES in _codes(report)
        v = next(v for v in report.violations
                 if v.code == CODE_POI_NOT_IN_CANDIDATES)
        assert v.level == LEVEL_ERROR
        assert v.detail["poi_id"] == "ghost"

    def test_all_members_pass(self):
        insider = make_poi(poi_id="p1")
        day = make_day(items=[make_item(poi=insider)])
        report = check_itinerary(make_itinerary(days=[day]),
                                 valid_poi_ids={"p1"})
        assert CODE_POI_NOT_IN_CANDIDATES not in _codes(report)

    def test_no_pool_fact_is_not_evaluable(self):
        # 无候选池事实（None）→ 轴跳过：既不报也不放行语义，留给上层
        outsider = make_poi(poi_id="ghost")
        day = make_day(items=[make_item(poi=outsider)])
        report = check_itinerary(make_itinerary(days=[day]), valid_poi_ids=None)
        assert CODE_POI_NOT_IN_CANDIDATES not in _codes(report)

    def test_empty_pool_set_still_validates(self):
        # 空集合是「明确没有合法成员」的事实 → 行程内任何 POI 都应报
        day = make_day(items=[make_item(poi=make_poi(poi_id="p1"))])
        report = check_itinerary(make_itinerary(days=[day]), valid_poi_ids=set())
        assert CODE_POI_NOT_IN_CANDIDATES in _codes(report)


# ============================================================
# 全行程重复结构（T12）
# ============================================================
class TestCrossDayDuplicate:
    def test_same_poi_across_days_is_error(self):
        poi = make_poi(poi_id="p1", name="鼓浪屿")
        d1 = make_day(day_index=1, items=[make_item(poi=poi)])
        d2 = make_day(day_index=2,
                      items=[make_item(poi=make_poi(poi_id="p1", name="鼓浪屿"))])
        report = check_itinerary(
            make_itinerary(brief=TravelBrief(destination="厦门", days=2),
                           days=[d1, d2]),
            valid_poi_ids={"p1"},
        )
        assert CODE_POI_DUPLICATED in _codes(report)
        v = next(v for v in report.violations if v.code == CODE_POI_DUPLICATED)
        assert v.level == LEVEL_ERROR
        assert v.day_index == 2  # 后现天被点名，首现天不动

    def test_same_day_duplicate_keeps_legacy_warning(self):
        # 同天重复保持既有语义（GEO_REVISIT warning），与新 error 分工
        poi = make_poi(poi_id="p1")
        day = make_day(items=[make_item(poi=poi),
                              make_item(start="11:00", end="12:30", poi=poi)])
        report = check_itinerary(make_itinerary(days=[day]), valid_poi_ids={"p1"})
        assert CODE_GEO_REVISIT in _codes(report)
        assert CODE_POI_DUPLICATED not in _codes(report)

    def test_distinct_pois_across_days_clean(self):
        d1 = make_day(day_index=1, items=[make_item(poi=make_poi(poi_id="p1"))])
        d2 = make_day(day_index=2, items=[make_item(poi=make_poi(poi_id="p2"))])
        report = check_itinerary(
            make_itinerary(brief=TravelBrief(destination="厦门", days=2),
                           days=[d1, d2]),
            valid_poi_ids={"p1", "p2"},
        )
        assert not report.errors


# ============================================================
# repair：两类新违反的真实修复
# ============================================================
class TestRepairNewViolations:
    def test_repair_removes_unknown_poi(self):
        outsider = make_poi(poi_id="ghost", name="虚构海湾艺术中心")
        day = make_day(items=[make_item(poi=outsider)])
        itinerary = make_itinerary(days=[day])
        report = check_itinerary(itinerary, valid_poi_ids=set())
        repaired, actions = repair_itinerary(itinerary, report)
        assert repaired is not None
        assert "ghost" not in {i.poi.poi_id for d in repaired.days
                               for i in d.items if i.poi}
        assert any(a.dropped == ["虚构海湾艺术中心"] for a in actions)

    def test_repair_dedupes_across_days_keeps_first(self):
        poi_d1 = make_poi(poi_id="p1", name="鼓浪屿", required=True)
        poi_d2 = make_poi(poi_id="p1", name="鼓浪屿", required=True)
        d1 = make_day(day_index=1, items=[make_item(poi=poi_d1)])
        d2 = make_day(day_index=2, items=[make_item(poi=poi_d2)])
        itinerary = make_itinerary(
            brief=TravelBrief(destination="厦门", days=2, must_go=["鼓浪屿"]),
            days=[d1, d2])
        report = check_itinerary(itinerary, valid_poi_ids={"p1"})
        repaired, actions = repair_itinerary(itinerary, report)
        assert repaired is not None
        days_with = [d.day_index for d in repaired.days
                     if any(i.poi and i.poi.poi_id == "p1" for i in d.items)]
        assert days_with == [1]  # 保留首现
        assert any(a.code == CODE_POI_DUPLICATED for a in actions)
        # 必去语义由首现满足：后续重复摘除时留痕
        assert any("鼓浪屿" in a.kept_required or "鼓浪屿" in a.dropped
                   for a in actions)


# ============================================================
# transit 专家：候选池外 id 显式留痕（不再静默蒸发）
# ============================================================
class TestTransitUnknownIdTrace:
    def test_unknown_id_in_day_plan_is_reported(self):
        from backend.travel.experts.transit import transit_expert_node

        pool = [make_poi(poi_id="p1", name="鼓浪屿").model_dump()]
        state = {
            "brief": TravelBrief(destination="厦门", days=1).model_dump(),
            "candidates": pool,
            "day_plan": [["p1", "ghost_id"]],
        }
        update = transit_expert_node(state)
        joined = "\n".join(update.get("notes", []))
        assert "候选数据外" in joined
        # 非法 id 未进入行程（排不出没有数据的地点），但事实已留痕
        it = update.get("itinerary") or {}
        scheduled = [i["poi"]["poi_id"] for d in it.get("days", [])
                     for i in d.get("items", []) if i.get("poi")]
        assert "ghost_id" not in scheduled
