"""tests/travel/test_validator.py — 硬约束校验器（四轴）

每个轴单独一组用例，并覆盖「该轴不该误报」的反面用例 ——
一个只会报错的校验器和一个不报错的校验器一样没用。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.tests.travel.conftest import (
    MONDAY,
    make_day,
    make_item,
    make_itinerary,
    make_leg,
    make_meal,
    make_poi,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.validation import (
    CODE_BUDGET_OVER,
    CODE_BUDGET_TIGHT,
    CODE_GEO_FAR_LEG,
    CODE_GEO_REVISIT,
    CODE_GEO_SCATTER,
    CODE_MUST_GO_MISSING,
    CODE_PACE_TOO_INTENSE,
    CODE_PACE_TOO_MANY_POIS,
    CODE_TIME_CLOSED,
    CODE_TIME_CLOSED_WEEKDAY,
    CODE_TIME_DAY_OVERRUN,
    CODE_TIME_LONG_WAIT,
    CODE_TIME_OVERLAP,
    LEVEL_ERROR,
    LEVEL_WARNING,
)
from backend.travel.validator import (
    check_budget,
    check_coverage,
    check_geo,
    check_itinerary,
    check_pace,
    check_time,
    compute_confidence,
)


def _codes(violations) -> list[str]:
    return [v.code for v in violations]


class TestTimeAxis:
    def test_visit_outside_opening_hours_is_error(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        day = make_day(items=[make_item(start="18:00", end="19:30", poi=poi)])
        got = check_time(make_itinerary(days=[day]))
        assert CODE_TIME_CLOSED in _codes(got)
        assert all(v.level == LEVEL_ERROR for v in got if v.code == CODE_TIME_CLOSED)

    def test_visit_inside_opening_hours_passes(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        day = make_day(items=[make_item(start="09:00", end="10:30", poi=poi)])
        assert check_time(make_itinerary(days=[day])) == []

    def test_visit_on_closed_weekday_is_error(self):
        poi = make_poi(closed_weekdays=(0,))  # 周一闭馆
        day = make_day(
            day_date=MONDAY,
            items=[make_item(start="09:00", end="10:30", poi=poi)],
        )
        assert CODE_TIME_CLOSED_WEEKDAY in _codes(
            check_time(make_itinerary(days=[day])))

    def test_closed_weekday_skipped_when_date_unknown(self):
        """未提供出发日期时无法判定闭馆日 —— 不得凭空报错。"""
        poi = make_poi(closed_weekdays=(0,))
        day = make_day(day_date=None,
                       items=[make_item(start="09:00", end="10:30", poi=poi)])
        assert check_time(make_itinerary(days=[day])) == []

    def test_open_weekday_passes(self):
        poi = make_poi(closed_weekdays=(0,))
        day = make_day(day_date=None or __import__("datetime").date(2026, 9, 15),
                       items=[make_item(start="09:00", end="10:30", poi=poi)])
        assert check_time(make_itinerary(days=[day])) == []

    def test_overlap_detected(self):
        a = make_poi(poi_id="a", name="A")
        b = make_poi(poi_id="b", name="B")
        day = make_day(items=[
            make_item(title="A", start="09:00", end="11:00", poi=a),
            make_item(title="B", start="10:30", end="12:00", poi=b),
        ])
        assert CODE_TIME_OVERLAP in _codes(check_time(make_itinerary(days=[day])))

    def test_adjacent_items_do_not_overlap(self):
        """首尾相接（11:00 结束、11:00 开始）不算重叠。"""
        a = make_poi(poi_id="a", name="A")
        b = make_poi(poi_id="b", name="B")
        day = make_day(items=[
            make_item(title="A", start="09:00", end="11:00", poi=a),
            make_item(title="B", start="11:00", end="12:00", poi=b),
        ])
        assert check_time(make_itinerary(days=[day])) == []

    def test_day_overrun_is_warning_not_error(self):
        poi = make_poi(open_time="09:00", close_time="23:00")
        day = make_day(items=[make_item(start="20:30", end="23:00", poi=poi)])
        got = check_time(make_itinerary(days=[day]))
        overrun = [v for v in got if v.code == CODE_TIME_DAY_OVERRUN]
        assert overrun and overrun[0].level == LEVEL_WARNING

    def test_long_wait_is_warning(self):
        poi = make_poi(name="美食街", open_time="11:00", close_time="23:00")
        day = make_day(items=[
            make_item(start="11:00", end="12:30", poi=poi,
                      wait_minutes=T.TRAVEL_LONG_WAIT_MINUTES + 5),
        ])
        got = check_time(make_itinerary(days=[day]))
        assert CODE_TIME_LONG_WAIT in _codes(got)


class TestGeoAxis:
    def test_far_leg_is_error(self):
        day = make_day(
            items=[make_item(title="A", start="09:00", end="10:00")],
            legs=[make_leg(minutes=T.TRAVEL_MAX_LEG_MINUTES_ERR)],
        )
        got = check_geo(make_itinerary(days=[day]))
        assert CODE_GEO_FAR_LEG in _codes(got)
        assert got[0].level == LEVEL_ERROR

    def test_medium_leg_is_warning(self):
        day = make_day(
            items=[make_item(title="A", start="09:00", end="10:00")],
            legs=[make_leg(minutes=T.TRAVEL_MAX_LEG_MINUTES_WARN)],
        )
        got = check_geo(make_itinerary(days=[day]))
        assert got and got[0].level == LEVEL_WARNING

    def test_short_leg_passes(self):
        day = make_day(
            items=[make_item(title="A", start="09:00", end="10:00")],
            legs=[make_leg(minutes=T.TRAVEL_MAX_LEG_MINUTES_WARN - 1)],
        )
        assert check_geo(make_itinerary(days=[day])) == []

    def test_scatter_when_transit_total_exceeds_limit(self):
        legs = [make_leg(minutes=40) for _ in range(4)]  # 160 > 150
        day = make_day(items=[make_item(title="A", start="09:00", end="10:00")],
                       legs=legs)
        got = check_geo(make_itinerary(days=[day]))
        assert CODE_GEO_SCATTER in _codes(got)
        assert all(v.level == LEVEL_ERROR for v in got if v.code == CODE_GEO_SCATTER)

    def test_revisit_is_warning(self):
        poi = make_poi(poi_id="x", name="X")
        day = make_day(items=[
            make_item(title="X", start="09:00", end="10:00", poi=poi),
            make_item(title="X", start="11:00", end="12:00", poi=poi),
        ])
        got = check_geo(make_itinerary(days=[day]))
        assert CODE_GEO_REVISIT in _codes(got)
        assert got[0].level == LEVEL_WARNING


class TestPaceAxis:
    def test_too_many_pois(self):
        limit = T.TRAVEL_PACE_MAX_POIS["moderate"]
        items = [make_item(title=f"P{i}", start=f"{9+i:02d}:00",
                           end=f"{9+i:02d}:30", poi=make_poi(poi_id=f"p{i}"))
                 for i in range(limit + 1)]
        got = check_pace(make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="moderate"),
            days=[make_day(items=items)]))
        assert CODE_PACE_TOO_MANY_POIS in _codes(got)

    def test_too_intense(self):
        limit = T.TRAVEL_PACE_MINUTES["moderate"]
        poi = make_poi(suggested_minutes=limit + 30)
        day = make_day(items=[
            make_item(start="09:00", end="15:00", poi=poi,
                      )])
        day.active_minutes = limit + 30
        got = check_pace(make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="moderate"),
            days=[day]))
        assert CODE_PACE_TOO_INTENSE in _codes(got)

    def test_meal_time_not_counted_as_activity(self):
        """用餐不得计入活动强度 —— 否则一顿饭吃掉 60 分钟预算。"""
        day = make_day(items=[
            make_item(start="09:00", end="10:00", poi=make_poi()),
            make_meal(start="12:00", end="13:00"),
        ])
        assert day.active_minutes == 60

    def test_relaxed_pace_is_stricter(self):
        item = make_item(start="09:00", end="13:30", poi=make_poi())  # 270 分钟
        day = make_day(items=[item])
        relaxed = check_pace(make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="relaxed"),
            days=[day]))
        moderate = check_pace(make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="moderate"),
            days=[day]))
        assert CODE_PACE_TOO_INTENSE in _codes(relaxed)
        assert CODE_PACE_TOO_INTENSE not in _codes(moderate)


class TestBudgetAxis:
    def test_no_budget_skips_check(self):
        it = make_itinerary(brief=TravelBrief(destination="测试城", days=1))
        assert check_budget(it) == []

    def test_over_budget_is_error(self):
        it = make_itinerary(brief=TravelBrief(destination="测试城", days=1,
                                              budget_cny=100))
        it.cost.tickets = 500.0
        got = check_budget(it)
        assert _codes(got) == [CODE_BUDGET_OVER]
        assert got[0].level == LEVEL_ERROR
        assert got[0].detail["over"] == 400.0

    def test_tight_budget_is_warning(self):
        it = make_itinerary(brief=TravelBrief(destination="测试城", days=1,
                                              budget_cny=100))
        it.cost.tickets = 95.0
        got = check_budget(it)
        assert _codes(got) == [CODE_BUDGET_TIGHT]
        assert got[0].level == LEVEL_WARNING

    def test_within_budget_passes(self):
        it = make_itinerary(brief=TravelBrief(destination="测试城", days=1,
                                              budget_cny=1000))
        it.cost.tickets = 100.0
        assert check_budget(it) == []


class TestCoverageAxis:
    def test_missing_must_go_is_warning(self):
        poi = make_poi(name="别的景点")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["想去的地方"]),
            days=[make_day(items=[make_item(title="别的景点", poi=poi)])])
        got = check_coverage(it)
        assert _codes(got) == [CODE_MUST_GO_MISSING]
        assert got[0].level == LEVEL_WARNING

    def test_present_must_go_passes(self):
        poi = make_poi(name="三坊七巷")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["三坊七巷"]),
            days=[make_day(items=[make_item(title="三坊七巷", poi=poi)])])
        assert check_coverage(it) == []


class TestReportAndConfidence:
    def test_report_passed_only_when_no_errors(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        clean = make_itinerary(days=[make_day(
            items=[make_item(start="09:00", end="10:30", poi=poi)])])
        assert check_itinerary(clean).passed is True

        broken = make_itinerary(days=[make_day(
            items=[make_item(start="18:00", end="19:30", poi=poi)])])
        assert check_itinerary(broken).passed is False

    def test_report_counts_checked_days(self):
        it = make_itinerary(days=[make_day(day_index=1), make_day(day_index=2)])
        assert check_itinerary(it).checked_days == 2

    def test_codes_are_deduped_and_ordered(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        it = make_itinerary(days=[
            make_day(day_index=1, items=[make_item(start="18:00", end="19:00", poi=poi)]),
            make_day(day_index=2, items=[make_item(start="18:00", end="19:00", poi=poi)]),
        ])
        report = check_itinerary(it)
        assert report.codes().count(CODE_TIME_CLOSED) == 1
        assert len(report.errors) == 2  # 但违反本身有两条

    def test_confidence_drops_with_errors_and_seed_data(self):
        clean = make_itinerary(days=[make_day(
            items=[make_item(start="09:00", end="10:30",
                             poi=make_poi(source="mcp:tencent-map"))])])
        clean_report = check_itinerary(clean)
        high = compute_confidence(clean, clean_report)

        broken = make_itinerary(days=[make_day(
            items=[make_item(start="18:00", end="19:30", poi=make_poi())])])
        low = compute_confidence(broken, check_itinerary(broken))
        assert low < high
        assert 0.0 <= low <= 1.0
