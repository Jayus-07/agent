"""tests/travel/test_repair.py — 修复器取舍纪律

最关键的一条：**用户点名必去的条目永不被静默丢弃**。
命中违规时保留并记为 kept_required，由 reporter 如实告知。
其余用例覆盖各类违规的动作选择是否正确。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.tests.travel.conftest import (
    MONDAY,
    make_day,
    make_item,
    make_itinerary,
    make_poi,
)
from backend.travel.models.brief import TravelBrief
from backend.travel.repair import repair_itinerary
from backend.travel.validator import check_itinerary


class TestClosedTimeRepair:
    def test_drops_non_required_item(self):
        poi = make_poi(poi_id="late", name="深夜馆", open_time="09:00",
                       close_time="17:00")
        keep = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                        close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=keep),
                make_item(title="深夜馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        names = [i.title for d in repaired.days for i in d.items]
        assert "深夜馆" not in names
        assert "正常馆" in names
        assert any("深夜馆" in a.dropped for a in actions)
        assert repaired.repair_rounds == 1

    def test_required_item_is_kept_and_logged(self):
        poi = make_poi(poi_id="must", name="必去馆", open_time="09:00",
                       close_time="17:00", required=True)
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, must_go=["必去馆"]),
            days=[make_day(items=[
                make_item(title="必去馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        kept = [a for a in actions if a.kept_required]
        assert kept and kept[0].kept_required == ["必去馆"]
        # 必去项未被丢弃 → 无需重建行程
        assert repaired is None

    def test_closed_weekday_repair(self):
        poi = make_poi(poi_id="mon", name="周一闭馆馆",
                       closed_weekdays=(0,), open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(day_date=MONDAY, items=[
                make_item(title="周一闭馆馆", start="09:00", end="10:30", poi=poi),
            ])],
        )
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        assert repaired.days == []


class TestPaceAndBudgetRepair:
    def test_pace_repair_drops_lowest_rated(self):
        pois = [
            make_poi(poi_id=f"p{i}", name=f"P{i}", rating=4.0 + i * 0.1,
                     suggested_minutes=100)
            for i in range(T.TRAVEL_PACE_MAX_POIS["moderate"] + 1)
        ]
        items = [make_item(title=p.name, start=f"{9+i:02d}:00",
                           end=f"{10+i:02d}:40", poi=p)
                 for i, p in enumerate(pois)]
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, pace="moderate"),
            days=[make_day(items=items)],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        # 最低分的 p0 应被移除
        assert any("P0" in a.dropped for a in actions)
        remaining = [i.title for d in repaired.days for i in d.items]
        assert "P0" not in remaining

    def test_budget_repair_drops_most_expensive_ticket(self):
        cheap = make_poi(poi_id="cheap", name="便宜馆", ticket_cny=20.0,
                         open_time="09:00", close_time="17:00")
        pricey = make_poi(poi_id="pricey", name="昂贵馆", ticket_cny=800.0,
                          open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, budget_cny=100),
            days=[make_day(items=[
                make_item(title="便宜馆", start="09:00", end="10:00", poi=cheap),
                make_item(title="昂贵馆", start="10:00", end="11:00", poi=pricey),
            ])],
        )
        # 预算校验读的是行程自身的费用汇总（而非重算），故需显式写入
        it.cost.tickets = 820.0
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        assert any("昂贵馆" in a.dropped for a in actions)
        remaining = [i.title for d in repaired.days for i in d.items]
        assert "便宜馆" in remaining and "昂贵馆" not in remaining
        # 修复后费用按幸存项目重算，不再带着已删除项目的票价
        assert repaired.cost.tickets == 20.0

    def test_budget_repair_skips_free_items(self):
        """超预算但全是免费项目 → 无可移除项，返回 None（交由 reporter 说明）。"""
        free = make_poi(poi_id="free", name="免费馆", ticket_cny=0.0)
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1, budget_cny=1),
            days=[make_day(items=[
                make_item(title="免费馆", start="09:00", end="10:00", poi=free),
            ])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is None
        assert actions == []

    def test_nothing_to_repair_returns_none(self):
        poi = make_poi(open_time="09:00", close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(start="09:00", end="10:00", poi=poi)])],
        )
        repaired, actions = repair_itinerary(it, check_itinerary(it))
        assert repaired is None and actions == []


class TestRepairPreservesContract:
    def test_repair_preserves_day_index_and_date(self):
        poi = make_poi(poi_id="bad", name="超晚馆", open_time="09:00",
                       close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=2),
            days=[
                make_day(day_index=1, day_date=MONDAY, items=[
                    make_item(title="正常馆", start="09:00", end="10:30", poi=ok)]),
                make_day(day_index=2, day_date=None, items=[
                    make_item(title="超晚馆", start="18:00", end="19:30", poi=poi)]),
            ],
        )
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired is not None
        # 第 2 天被清空 → 只保留第 1 天，且 day_index/date 未被重排
        assert len(repaired.days) == 1
        assert repaired.days[0].day_index == 1
        assert repaired.days[0].day_date == MONDAY

    def test_repair_recomputes_cost(self):
        poi = make_poi(poi_id="bad", name="超晚馆", ticket_cny=100.0,
                       open_time="09:00", close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="超晚馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        before = it.days[0].cost_cny
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired.cost.total > 0
        assert repaired.days[0].cost_cny != before

    def test_repair_carries_sources_and_warnings(self):
        poi = make_poi(poi_id="bad", name="超晚馆", open_time="09:00",
                       close_time="17:00")
        ok = make_poi(poi_id="ok", name="正常馆", open_time="09:00",
                      close_time="17:00")
        it = make_itinerary(
            brief=TravelBrief(destination="测试城", days=1),
            days=[make_day(items=[
                make_item(title="正常馆", start="09:00", end="10:30", poi=ok),
                make_item(title="超晚馆", start="18:00", end="19:30", poi=poi),
            ])],
        )
        it.sources = ["seed:local"]
        it.warnings = ["示例数据声明"]
        repaired, _ = repair_itinerary(it, check_itinerary(it))
        assert repaired.sources == ["seed:local"]
        assert repaired.warnings == ["示例数据声明"]
