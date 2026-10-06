"""STOP 6：规划质量验收的可重复契约测试。"""
from __future__ import annotations

from datetime import date

from backend.tests.travel.conftest import make_day, make_item, make_itinerary, make_poi
from backend.travel.agents.requirement_agent import (
    extract_budget_constraint,
    extract_fresh_brief,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import CostBreakdown
from backend.travel.models.validation import (
    CODE_BUDGET_OVER,
    CODE_BUDGET_SOFT_OVER,
    CODE_TIME_BEFORE_ARRIVAL,
    LEVEL_ERROR,
    LEVEL_WARNING,
)
from backend.travel.services.requirement_service import merge_brief
from backend.travel.services.transit_service import schedule_day
from backend.travel.stability import compare_plan_scope
from backend.travel.validator import check_budget, check_time
from backend.travel.experts import weather as weather_expert


def _codes(items) -> list[str]:
    return [item.code for item in items]


def test_budget_constraint_distinguishes_hard_and_soft_language():
    assert extract_budget_constraint("预算不能超过 2000") == "hard"
    assert extract_budget_constraint("预算大概 3000") == "soft"
    assert extract_budget_constraint("去杭州玩三天") is None


def test_budget_constraint_is_structured_in_brief_and_merge():
    hard = extract_fresh_brief("去杭州玩三天，预算不能超过 2000")
    soft = extract_fresh_brief("预算大概 3000")
    assert hard.budget_cny == 2000
    assert hard.budget_constraint == "hard"
    assert soft.budget_cny == 3000
    assert soft.budget_constraint == "soft"
    merged = merge_brief(hard, soft)
    assert merged.budget_constraint == "soft"


def test_arrival_time_has_buffer_and_validator_blocks_early_activity():
    brief = TravelBrief(destination="杭州", days=1, arrival_time="16:10")
    poi = make_poi(open_time="09:00", close_time="23:00")
    day = schedule_day([poi], 1, None, brief)
    assert day.items[0].start >= "16:40"

    early = make_itinerary(
        brief=brief,
        days=[make_day(items=[make_item(start="16:20", end="17:20", poi=poi)])],
    )
    violations = check_time(early)
    before = [item for item in violations if item.code == CODE_TIME_BEFORE_ARRIVAL]
    assert before and before[0].level == LEVEL_ERROR


def test_hard_budget_blocks_but_soft_budget_explains_overage():
    poi = make_poi(ticket_cny=500)
    hard = make_itinerary(
        brief=TravelBrief(destination="杭州", days=1, budget_cny=200,
                          budget_constraint="hard"),
        days=[make_day(items=[make_item(poi=poi)])],
    )
    hard.cost = CostBreakdown(tickets=500)
    soft = hard.model_copy(deep=True)
    soft.brief.budget_constraint = "soft"
    assert CODE_BUDGET_OVER in _codes(check_budget(hard))
    soft_over = check_budget(soft)
    assert CODE_BUDGET_SOFT_OVER in _codes(soft_over)
    assert all(item.level == LEVEL_WARNING for item in soft_over)


def test_conditional_weather_is_structured_and_scope_is_local():
    brief = extract_fresh_brief("杭州三天，第二天下雨就安排室内")
    assert brief.weather_conditions == [
        {"day_index": 2, "condition": "rain", "action": "indoor"}
    ]

    day1 = make_day(1, [make_item(title="A", poi=make_poi(poi_id="a"))])
    day2 = make_day(2, [make_item(title="B", poi=make_poi(poi_id="b"))])
    old = make_itinerary(brief=TravelBrief(destination="杭州", days=2),
                         days=[day1, day2])
    new = old.model_copy(deep=True)
    new.days[1].items[0].title = "室内馆"
    new.days[1].items[0].poi.name = "室内馆"
    new.days[1].items[0].poi.poi_id = "indoor"
    scope = compare_plan_scope(old, new)
    assert scope["preserved_days"] == [1]
    assert scope["changed_days"] == [2]


def test_conditional_weather_reads_forecast_and_changes_only_target_day(monkeypatch):
    start = date(2026, 9, 15)
    brief = TravelBrief(
        destination="杭州", days=2, start_date=start,
        weather_conditions=[{"day_index": 2, "condition": "rain", "action": "indoor"}],
    )
    day1 = make_day(1, [make_item(title="户外一", poi=make_poi(
        poi_id="out-1", name="户外一", category="公园"))],
                    day_date=start)
    day2 = make_day(2, [make_item(title="户外二", poi=make_poi(
        poi_id="out-2", name="户外二", category="公园"))],
                    day_date=date(2026, 9, 16))
    itinerary = make_itinerary(brief=brief, days=[day1, day2])
    indoor = make_poi(poi_id="in-1", name="博物馆", category="博物馆")
    monkeypatch.setattr(
        weather_expert, "fetch_forecast_evidence",
        lambda _city: ({"days": [
            {"date": "2026-09-15", "day": {"weather": "中雨"}},
            {"date": "2026-09-16", "day": {"weather": "中雨"}},
        ]}, "", None),
    )
    update = weather_expert.weather_expert_node({
        "brief": brief.model_dump(),
        "itinerary": itinerary.model_dump(),
        "candidates": [indoor.model_dump()],
    })
    changed = update["itinerary"]
    assert changed["days"][0]["items"][0]["poi"]["poi_id"] == "out-1"
    assert changed["days"][1]["items"][0]["poi"]["poi_id"] == "in-1"
    assert any("条件天气" in note for note in update["notes"])
