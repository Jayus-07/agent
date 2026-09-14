"""tests/travel/conftest.py — 旅游域测试公共构造器

用工厂函数而非 fixture 字典：测试里要临时改某一个字段（如把开放时间
改成 18:00 来触发 TIME_CLOSED）时，工厂的默认参数比复制一坨字典清晰得多。
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.travel.models.brief import TravelBrief
from backend.travel.models.itinerary import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    KIND_MEAL,
    KIND_VISIT,
    TransitLeg,
)
from backend.travel.models.poi import Poi

# 2026-09-14 是周一（weekday()==0）—— 用于闭馆日判定
MONDAY = date(2026, 9, 14)
TUESDAY = date(2026, 9, 15)


def make_poi(
    poi_id: str = "p1",
    name: str = "测试景点",
    open_time: str = "09:00",
    close_time: str = "17:00",
    closed_weekdays: tuple[int, ...] = (),
    suggested_minutes: int = 90,
    ticket_cny: float = 0.0,
    required: bool = False,
    rating: float = 4.0,
    lat: float = 26.0,
    lng: float = 119.0,
    source: str = "seed:local",
    category: str = "景点",
    tags: list[str] | None = None,
) -> Poi:
    return Poi(
        poi_id=poi_id, name=name, city="测试城", category=category,
        lat=lat, lng=lng, open_time=open_time, close_time=close_time,
        closed_weekdays=list(closed_weekdays),
        suggested_minutes=suggested_minutes, ticket_cny=ticket_cny,
        tags=tags or [], rating=rating, required=required, source=source,
    )


def make_item(
    title: str = "测试景点",
    start: str = "09:00",
    end: str = "10:30",
    kind: str = KIND_VISIT,
    poi: Poi | None = None,
    wait_minutes: int = 0,
    note: str = "",
) -> ItineraryItem:
    return ItineraryItem(
        title=title, kind=kind, start=start, end=end,
        minutes=_diff(start, end), wait_minutes=wait_minutes,
        poi=poi, note=note,
    )


def make_meal(start: str = "12:00", end: str = "13:00") -> ItineraryItem:
    return make_item(title="午餐", start=start, end=end, kind=KIND_MEAL, poi=None)


def _diff(start: str, end: str) -> int:
    def _m(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    return max(0, _m(end) - _m(start))


def make_leg(
    from_title: str = "A", to_title: str = "B",
    minutes: int = 20, distance_km: float = 5.0,
    mode: str = "drive", cost_cny: float = 25.0,
) -> TransitLeg:
    return TransitLeg(
        from_title=from_title, to_title=to_title, minutes=minutes,
        distance_km=distance_km, mode=mode, cost_cny=cost_cny,
    )


def make_day(
    day_index: int = 1,
    items: list[ItineraryItem] | None = None,
    legs: list[TransitLeg] | None = None,
    day_date: date | None = None,
) -> ItineraryDay:
    day = ItineraryDay(
        day_index=day_index, day_date=day_date,
        items=items or [], legs=legs or [],
    )
    # 与 schedule_day 保持同一口径：活动时长只计到访项
    day.active_minutes = sum(i.minutes for i in day.items if i.kind == KIND_VISIT)
    day.transit_minutes = sum(leg.minutes for leg in day.legs)
    return day


def make_itinerary(
    brief: TravelBrief | None = None,
    days: list[ItineraryDay] | None = None,
) -> Itinerary:
    return Itinerary(brief=brief or TravelBrief(destination="测试城", days=1),
                     days=days or [])


@pytest.fixture
def simple_brief() -> TravelBrief:
    return TravelBrief(destination="测试城", days=1, party_size=1, pace="moderate")
