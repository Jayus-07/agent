"""schedule_day 排程衔接缓冲（travel-proto T2）——排程与写校验器同口径回归。

背景（2026-11-02 T2 立项，真机 4+ 轮实证）：schedule_day 的排程光标只推进
到上一项结束时刻，已产出的 leg.minutes 车程没有计入，下一项 start = 上一项
end；而服务端 trip_write_validator 要求下一项 start ≥ 上一项 end +
leg.duration_min（reliability ∈ verified/estimated 时），生成草稿因此必然
触发 SCHEDULE_OVERLAP（422）。根因在确定性排程器，不在 LLM 提示词——
start_time 全部由 schedule_day 产出，LLM 只决定分天。

本文件钉住两条不变量：
1. 排程产出必须能通过 trip_write_validator 的 SCHEDULE_OVERLAP 判定
   （以 validator 本体做断言，口径零漂移）；
2. 开门等候语义不被车程污染（wait_minutes 只计到门口后的等待）。
"""
from __future__ import annotations

from datetime import date

from backend.tools.travel import routing
from backend.travel.models.itinerary import ItineraryDay, KIND_VISIT
from backend.travel.models.brief import TravelBrief
from backend.travel.services.transit_service import schedule_day
from backend.travel_v2.services.trip_write_validator import (
    TripWriteConflictError,
    validate_trip_write,
)
from backend.tests.travel.conftest import make_poi

# 与 P4a 真机现象同构：相邻两站车程 27 分钟
DRIVE_MINUTES = 27


def _drive_provider(minutes: int = DRIVE_MINUTES):
    def provider(*_args, **_kwargs) -> dict:
        return {
            "distance_km": 12.0, "mode": "drive", "minutes": minutes,
            "cost_cny": 30.0, "source": "tencent:lbs",
        }
    return provider


def _nearby_pois():
    # 景点A/B 相距足够远（驱动注入 provider 的车程），都整日开门排除等候干扰
    return [
        make_poi(poi_id="a", name="景点A", open_time="07:00", lat=26.0, lng=119.0),
        make_poi(poi_id="b", name="景点B", open_time="07:00", lat=26.4, lng=119.5),
    ]


def _brief(days: int = 1) -> TravelBrief:
    return TravelBrief(destination="测试城", days=days)


def _to_validator_document(day: ItineraryDay) -> dict:
    """把 schedule_day 单日产出映射为 trip_write_validator 消费的 document。

    只映射 validator 检查的字段（days/items/legs）；POI 未带 verified
    opening_hours，VERIFIED_* 分支天然不触发，与本回归无关。
    """
    items: list[dict] = []
    id_by_title: dict[str, str] = {}
    for index, item in enumerate(day.items, start=1):
        item_id = f"it-{index}"
        id_by_title[item.title] = item_id
        items.append({
            "item_id": item_id,
            "title": item.title,
            "start_time": item.start,
            "duration_min": item.minutes,
        })
    legs = [{
        "from_item_id": id_by_title[leg.from_title],
        "to_item_id": id_by_title[leg.to_title],
        "duration_min": leg.minutes,
        # adapter 同口径：is_estimate=False → verified，否则 estimated；
        # 两者都会被 validator 计入车程缓冲。
        "reliability": "estimated" if leg.is_estimate else "verified",
    } for leg in day.legs]
    return {"days": [{"day_id": "d1", "date": None, "items": items, "legs": legs}]}


class TestScheduleTravelBuffer:
    def test_schedule_output_passes_v2_validator(self):
        """核心回归：排程产出（含午餐补插）必须通过 SCHEDULE_OVERLAP 判定。"""
        routing.set_route_provider(_drive_provider())
        try:
            day = schedule_day(_nearby_pois(), 1, date(2026, 11, 16), _brief())
        finally:
            routing.set_route_provider(None)
        validate_trip_write(_to_validator_document(day))

    def test_adjacent_start_reserves_leg_minutes(self):
        """相邻项 start ≥ 上一项 end + leg.minutes（车程计入光标）。"""
        routing.set_route_provider(_drive_provider())
        try:
            day = schedule_day(_nearby_pois(), 1, date(2026, 11, 16), _brief())
        finally:
            routing.set_route_provider(None)
        visits = [item for item in day.items if item.kind == KIND_VISIT]
        assert len(visits) == 2
        legs = {(leg.from_title, leg.to_title): leg.minutes for leg in day.legs}
        for prev, nxt in zip(visits, visits[1:]):
            travel = legs.get((prev.title, nxt.title))
            assert travel is not None, "相邻到访项之间应有路线 leg"
            assert nxt.start >= prev.end, "衔接时刻不得早于上一项结束"
            assert _to_minutes(nxt.start) >= _to_minutes(prev.end) + travel

    def test_zero_minutes_leg_keeps_prev_behavior(self):
        """车程为 0（V2 映射 unavailable，validator 不计入）时不额外等待。"""
        routing.set_route_provider(_drive_provider(minutes=0))
        try:
            day = schedule_day(_nearby_pois(), 1, date(2026, 11, 16), _brief())
        finally:
            routing.set_route_provider(None)
        visits = [item for item in day.items if item.kind == KIND_VISIT]
        assert _to_minutes(visits[1].start) >= _to_minutes(visits[0].end)

    def test_opening_wait_not_inflated_by_travel(self):
        """wait_minutes 只计开门等候：车程后早到门口的等待，不含车程本身。"""
        routing.set_route_provider(_drive_provider())
        try:
            pois = [
                make_poi(poi_id="a", name="景点A", open_time="07:00",
                         suggested_minutes=60, lat=26.0, lng=119.0),
                # B 12:00 才开门：A 08:30+60 → 09:30 end，+27 车程 = 09:57 到
                # 门口，等候 123 分钟到 12:00 开门
                make_poi(poi_id="b", name="景点B", open_time="12:00",
                         lat=26.4, lng=119.5),
            ]
            day = schedule_day(pois, 1, date(2026, 11, 16), _brief())
        finally:
            routing.set_route_provider(None)
        visits = [item for item in day.items if item.kind == KIND_VISIT]
        target = next(item for item in visits if item.title == "景点B")
        assert target.wait_minutes == 123
        assert target.start == "12:00"

    def test_meal_insertion_keeps_buffer_chain(self):
        """午餐补插不破坏衔接链：午餐 start ≥ 上一项 end，午餐后仍预留车程。"""
        routing.set_route_provider(_drive_provider())
        try:
            pois = [
                # A 08:30–12:30（240 分钟），cursor 落入午餐窗口 → A 后插午餐
                make_poi(poi_id="a", name="景点A", open_time="07:00",
                         suggested_minutes=240, lat=26.0, lng=119.0),
                make_poi(poi_id="b", name="景点B", open_time="07:00",
                         lat=26.4, lng=119.5),
            ]
            day = schedule_day(pois, 1, date(2026, 11, 16), _brief())
        finally:
            routing.set_route_provider(None)
        validate_trip_write(_to_validator_document(day))
        titles = [item.title for item in day.items]
        assert titles.index("午餐") < titles.index("景点B"), "午餐应插在 A 与 B 之间"
        meal = next(item for item in day.items if item.title == "午餐")
        assert _to_minutes(meal.start) >= _to_minutes(
            next(item for item in day.items if item.title == "景点A").end,
        )
        visit_b = next(item for item in day.items if item.title == "景点B")
        assert _to_minutes(visit_b.start) >= _to_minutes(meal.end) + DRIVE_MINUTES


def _to_minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)
