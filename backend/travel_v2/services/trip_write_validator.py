from __future__ import annotations

from datetime import date, time
from typing import Any


def validate_trip_write(
    document: dict[str, Any],
    *,
    current_document: dict[str, Any] | None = None,
) -> None:
    """验证正式 Trip 写入的日程硬约束及跨版本保护规则。

    调用方应先通过 TripDocumentV2 的 Schema 校验。此函数只负责领域规则，
    并在任何 Trip、Revision 或操作账本写入之前执行。
    """
    _validate_schedule(document)
    if current_document is not None:
        _validate_protected_items(current_document, document)


def _validate_schedule(document: dict[str, Any]) -> None:
    for day in document["days"]:
        previous: tuple[dict[str, Any], int] | None = None
        for item in day["items"]:
            start_value = item.get("start_time")
            if start_value is None:
                continue
            start = time.fromisoformat(start_value)
            start_minute = start.hour * 60 + start.minute
            end_minute = start_minute + int(item.get("duration_min") or 0)
            if end_minute > 24 * 60:
                raise ValueError(f"行程项 {item['title']} 超出当天时间范围")

            if previous is not None:
                previous_item, previous_end = previous
                required_start = previous_end
                leg = next((
                    value for value in day["legs"]
                    if value["from_item_id"] == previous_item["item_id"]
                    and value["to_item_id"] == item["item_id"]
                ), None)
                if leg and leg.get("reliability") in {"verified", "estimated"}:
                    known_travel_min = leg.get("duration_min")
                    if known_travel_min is not None:
                        required_start += int(known_travel_min)
                if start_minute < required_start:
                    raise ValueError(
                        f"{previous_item['title']} 与 {item['title']} 的时间或路程安排冲突"
                    )

            _validate_opening_hours(day, item, start_minute, end_minute)
            previous = (item, end_minute)


def _validate_opening_hours(
    day: dict[str, Any], item: dict[str, Any], start: int, end: int,
) -> None:
    place = item.get("place") or {}
    facts = place.get("facts") or {}
    if facts.get("verification") != "verified":
        return
    hours = facts.get("opening_hours")
    if not isinstance(hours, dict):
        return
    day_date = day.get("date")
    if day_date:
        weekday = (
            date.fromisoformat(day_date) if isinstance(day_date, str) else day_date
        ).weekday()
        closed = hours.get("closed_weekdays") or []
        if weekday in closed or str(weekday) in closed:
            raise ValueError(f"{item['title']} 在行程日期闭馆")
    open_value = hours.get("open_time")
    close_value = hours.get("close_time")
    if not open_value or not close_value:
        return
    try:
        open_time = time.fromisoformat(str(open_value))
        close_time = time.fromisoformat(str(close_value))
    except ValueError:
        return
    open_minute = open_time.hour * 60 + open_time.minute
    close_minute = close_time.hour * 60 + close_time.minute
    if start < open_minute or end > close_minute:
        raise ValueError(f"{item['title']} 的安排超出已核实营业时间")


def _validate_protected_items(
    current: dict[str, Any], proposed: dict[str, Any],
) -> None:
    old_items = {
        item["item_id"]: (day["day_id"], item)
        for day in current["days"] for item in day["items"]
    }
    new_items = {
        item["item_id"]: (day["day_id"], item)
        for day in proposed["days"] for item in day["items"]
    }
    for item_id, (old_day_id, old_item) in old_items.items():
        new = new_items.get(item_id)
        if old_item.get("locked"):
            if new is None:
                raise ValueError(f"locked item cannot be removed: {item_id}")
            if new[0] != old_day_id:
                raise ValueError(f"locked item cannot be moved: {item_id}")
            if not new[1].get("locked"):
                raise ValueError(f"locked item cannot be unlocked: {item_id}")
        if old_item.get("fixed_start"):
            if new is None or new[0] != old_day_id:
                raise ValueError(f"fixed item cannot be removed or moved: {item_id}")
            if new[1].get("start_time") != old_item.get("start_time"):
                raise ValueError(f"fixed item start time cannot change: {item_id}")
            if not new[1].get("fixed_start"):
                raise ValueError(f"fixed item cannot be unlocked: {item_id}")

    old_positions = {
        day["day_id"]: [item["item_id"] for item in day["items"]]
        for day in current["days"]
    }
    new_positions = {
        day["day_id"]: [item["item_id"] for item in day["items"]]
        for day in proposed["days"]
    }
    protected = {
        item_id
        for item_id, (_day_id, item) in old_items.items()
        if item.get("locked") or item.get("fixed_start")
    }
    for day_id, old_order in old_positions.items():
        new_order = new_positions.get(day_id, [])
        surviving = set(old_order).intersection(new_order)
        surviving_old = [item_id for item_id in old_order if item_id in surviving]
        surviving_new = [item_id for item_id in new_order if item_id in surviving]
        old_rank = {item_id: index for index, item_id in enumerate(surviving_old)}
        new_rank = {item_id: index for index, item_id in enumerate(surviving_new)}
        for protected_id in protected.intersection(surviving_old):
            old_before = [item_id for item_id in surviving_old if item_id != protected_id]
            for other_id in old_before:
                if (old_rank[other_id] < old_rank[protected_id]) != (
                    new_rank[other_id] < new_rank[protected_id]
                ):
                    raise ValueError(
                        f"locked or fixed item cannot be reordered: {protected_id}"
                    )


def validate_protected_items(
    current: dict[str, Any], proposed: dict[str, Any],
) -> None:
    """验证当前版本的锁定和固定项目约束。"""
    _validate_protected_items(current, proposed)
