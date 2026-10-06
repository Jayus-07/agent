"""旅游规划版本的局部稳定性计算。"""
from __future__ import annotations

from typing import Any


def _day_signatures(itinerary: Any) -> dict[int, tuple[str, ...]]:
    """取每日 POI 身份签名，不把时间/费用变化误判为换景点。"""
    if itinerary is None:
        return {}
    days = itinerary.get("days", []) if isinstance(itinerary, dict) else itinerary.days
    result: dict[int, tuple[str, ...]] = {}
    for day in days or []:
        raw_items = day.get("items", []) if isinstance(day, dict) else day.items
        ids: list[str] = []
        for item in raw_items or []:
            poi = item.get("poi") if isinstance(item, dict) else item.poi
            if not poi:
                continue
            poi_id = poi.get("poi_id") if isinstance(poi, dict) else poi.poi_id
            name = poi.get("name") if isinstance(poi, dict) else poi.name
            ids.append(str(poi_id or name or ""))
        day_index = day.get("day_index") if isinstance(day, dict) else day.day_index
        result[int(day_index)] = tuple(ids)
    return result


def compare_plan_scope(previous: Any, current: Any) -> dict[str, Any]:
    """返回 preserved_days/changed_days，供 state、Trace、前端共同消费。"""
    old = _day_signatures(previous)
    new = _day_signatures(current)
    all_days = sorted(set(old) | set(new))
    preserved = [day for day in all_days if day in old and day in new
                 and old[day] == new[day]]
    changed = [day for day in all_days if day not in preserved]
    return {
        "preserved_days": preserved,
        "changed_days": changed,
        "scope_expansion_reason": "" if not changed else "行程日内容发生变化",
    }


__all__ = ["compare_plan_scope"]
