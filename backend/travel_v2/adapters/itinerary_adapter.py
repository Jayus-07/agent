from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.travel_v2.models.trip import TripDocumentV2

_PLATFORM_TIMEZONE = "Asia/Shanghai"
_ITEM_NAMESPACE = NAMESPACE_URL


def itinerary_to_trip_document(
    itinerary: Any,
    *,
    previous_document: TripDocumentV2 | dict | None = None,
) -> TripDocumentV2:
    """把旅游 Agent 只读输出转换成新契约；不会读取或写入旧账本。

    This adapter is intentionally one-way. V2 persistence never stores the
    legacy Itinerary payload itself.
    """
    raw = _as_dict(itinerary)
    brief = _as_dict(raw.get("brief"))
    legacy_days = raw.get("days") or []
    if not brief.get("destination") or not legacy_days:
        raise ValueError("Agent 行程缺少目的地或每日安排，无法生成 V2 行程")

    prior = _previous(previous_document)
    prior_days_by_date = {
        day.date.isoformat(): day
        for day in (prior.days if prior else []) if day.date is not None
    }
    prior_days_by_index = {
        index: day for index, day in enumerate(prior.days if prior else [])
    }
    prior_items: dict[tuple[str, str], list[str]] = {}
    if prior:
        for day in prior.days:
            for item in day.items:
                place_id = item.place.place_id if item.place else ""
                key = (place_id or item.title.casefold(), item.kind)
                prior_items.setdefault(key, []).append(item.item_id)
    used_prior_items: set[str] = set()

    parsed_start_date = _parse_date(brief.get("start_date"))
    day_count = len(legacy_days)
    pace = {
        "relaxed": "relaxed", "moderate": "balanced", "balanced": "balanced",
        "intense": "intense",
    }.get(str(brief.get("pace") or "moderate"), "balanced")
    adults = brief.get("adults")
    children = brief.get("children")
    if adults is None:
        adults = max(1, int(brief.get("party_size") or 1) - int(children or 0))

    issue_rows: list[dict[str, str]] = []
    timezone_name = str(brief.get("timezone") or _PLATFORM_TIMEZONE)
    if not brief.get("timezone"):
        issue_rows.append({
            "code": "timezone_assumed", "severity": "warning",
            "message": (
                f"Agent 输出未提供时区，暂按平台默认 {timezone_name} 记录；"
                "如目的地不在该时区，请在行程中确认。"
            ),
        })

    days: list[dict] = []
    poi_refs: dict[str, tuple[str, str]] = {}
    used_day_ids: set[str] = set()
    for index, raw_day in enumerate(legacy_days, start=1):
        day = _as_dict(raw_day)
        day_date = _parse_date(day.get("day_date"))
        old_day = (
            prior_days_by_date.get(day_date.isoformat()) if day_date else None
        ) or prior_days_by_index.get(index - 1)
        day_id = old_day.day_id if old_day else _stable_id(
            f"travel-v2/day/{day_date.isoformat() if day_date else index}"
        )
        if day_id in used_day_ids:
            day_id = _stable_id(f"travel-v2/day/{day_id}/{index}")
        used_day_ids.add(day_id)
        items: list[dict] = []
        legacy_items = day.get("items") or []
        legacy_titles: dict[str, list[dict]] = {}
        for item_index, raw_item in enumerate(legacy_items):
            item = _as_dict(raw_item)
            poi = _as_dict(item.get("poi")) if item.get("poi") else None
            kind = "place" if poi else "activity"
            title = str(item.get("title") or (poi or {}).get("name") or "未命名安排")
            activity_type = None if poi else _activity_type(item.get("kind"))
            poi_id = str((poi or {}).get("poi_id") or "")
            match_key = (poi_id or title.casefold(), kind)
            reused_id = next((
                candidate for candidate in prior_items.get(match_key, [])
                if candidate not in used_prior_items
            ), None)
            item_id = reused_id or _stable_id(
                f"travel-v2/item/{day_id}/{item_index}/{poi_id}/{title.casefold()}"
            )
            used_prior_items.add(item_id)
            place = _place_snapshot(poi) if poi else None
            item_document = {
                "item_id": item_id,
                "kind": kind,
                "activity_type": activity_type,
                "title": title,
                "start_time": item.get("start"),
                "duration_min": max(0, int(item.get("minutes") or 0)),
                "fixed_start": bool(item.get("fixed_start", False)),
                "place": place,
                "must_visit": bool((poi or {}).get("required")) or title in (brief.get("must_go") or []),
                "locked": bool(item.get("locked", False)),
                "note": str(item.get("note") or ""),
            }
            items.append(item_document)
            legacy_titles.setdefault(title, []).append(item_document)
            if poi:
                poi_refs[poi_id or _selection_id(title)] = (
                    str((poi or {}).get("name") or title),
                    item_id,
                )

        item_by_id = {item["item_id"]: item for item in items}
        legs: list[dict] = []
        for legacy_leg in day.get("legs") or []:
            leg = _as_dict(legacy_leg)
            from_rows = legacy_titles.get(str(leg.get("from_title") or ""), [])
            to_rows = legacy_titles.get(str(leg.get("to_title") or ""), [])
            if len(from_rows) != 1 or len(to_rows) != 1:
                issue_rows.append({
                    "code": "legacy_leg_unmapped", "severity": "warning",
                    "message": "Agent 交通段无法唯一关联起终点，已省略该交通段。",
                })
                continue
            start_item, end_item = from_rows[0], to_rows[0]
            if start_item["item_id"] == end_item["item_id"]:
                continue
            mode = _leg_mode(leg.get("mode"))
            if mode is None:
                issue_rows.append({
                    "code": "legacy_leg_mode_unknown", "severity": "warning",
                    "message": "Agent 交通段的交通方式不在 V2 支持范围，已省略该交通段。",
                })
                continue
            start_place = start_item.get("place")
            end_place = end_item.get("place")
            has_coordinates = all(
                value and value.get("lat") is not None and value.get("lng") is not None
                for value in (start_place, end_place)
            )
            if not has_coordinates:
                reliability = "unavailable"
                duration = distance = cost = None
                source = observed_at = None
            else:
                is_estimate = bool(leg.get("is_estimate", True))
                reliability = "estimated" if is_estimate else "verified"
                duration = max(0, int(leg.get("minutes") or 0))
                distance = max(0, round(float(leg.get("distance_km") or 0) * 1000))
                cost = max(0.0, float(leg.get("cost_cny") or 0))
                source = str(leg.get("source") or "legacy-agent")
                observed_at = leg.get("observed_at")
            old_leg = None
            if old_day:
                old_leg = next((
                    row for row in old_day.legs
                    if row.from_item_id == start_item["item_id"]
                    and row.to_item_id == end_item["item_id"]
                ), None)
            leg_id = old_leg.leg_id if old_leg else _stable_id(
                f"travel-v2/leg/{start_item['item_id']}/{end_item['item_id']}"
            )
            legs.append({
                "leg_id": leg_id,
                "from_item_id": start_item["item_id"],
                "to_item_id": end_item["item_id"],
                "selected_mode": mode,
                "duration_min": duration,
                "distance_m": distance,
                "cost_cny": cost,
                "reliability": reliability,
                "source": source,
                "observed_at": observed_at,
            })

        days.append({
            "day_id": day_id,
            "date": day_date.isoformat() if day_date else None,
            "title": str(day.get("title") or f"第 {index} 天"),
            "items": items,
            "legs": legs,
        })

    selections = _selections(brief, poi_refs)
    warnings = raw.get("warnings") or []
    for index, warning in enumerate(warnings):
        message = str(warning).strip()
        if message:
            issue_rows.append({
                "code": f"agent_warning_{index + 1}",
                "severity": "warning", "message": message[:1000],
            })
    cost = _as_dict(raw.get("cost"))
    estimated_cost = cost.get("total")
    if estimated_cost is None:
        estimated_cost = sum(
            float(cost.get(key) or 0)
            for key in ("tickets", "meals", "lodging", "transit")
        )
    transit_min = sum(
        int(_as_dict(day).get("transit_minutes") or 0) for day in legacy_days
    )
    if transit_min == 0:
        transit_min = sum(
            int(leg["duration_min"] or 0)
            for day in days for leg in day["legs"]
        )
    distance_m_values = [
        int(leg["distance_m"] or 0)
        for day in days for leg in day["legs"]
        if leg["distance_m"] is not None
    ]
    health_status = "needs_attention" if issue_rows else "ok"
    document = {
        "schema_version": 2,
        "brief": {
            "origin": str(brief.get("origin") or ""),
            "destination": str(brief["destination"]),
            "timezone": timezone_name,
            "start_date": parsed_start_date.isoformat() if parsed_start_date else None,
            "day_count": day_count,
            "travelers": {
                "adults": max(1, int(adults)),
                "children": max(0, int(children or 0)),
            },
            "budget": {"amount": brief.get("budget_cny"), "currency": "CNY"},
            "pace": pace,
            "interests": [str(value) for value in brief.get("preferences") or []],
            "requirements": [
                str(value) for value in (brief.get("must_go") or [])
                + (brief.get("avoid") or [])
            ],
        },
        "selections": selections,
        "days": days,
        "totals": {
            "currency": "CNY", "estimated_cost": max(0.0, float(estimated_cost or 0)),
            "transit_min": max(0, transit_min),
            "distance_m": sum(distance_m_values) if distance_m_values else None,
        },
        "health": {"status": health_status, "issues": issue_rows},
    }
    return TripDocumentV2.model_validate(document)


def _place_snapshot(poi: dict) -> dict:
    source = str(poi.get("source") or "legacy-agent")
    raw_status = str(poi.get("verification_status") or "unknown").lower()
    if raw_status == "verified" and poi.get("location_status") != "unverified":
        verification = "verified"
    elif source.startswith("estimate:") or raw_status == "estimated":
        verification = "estimated"
    else:
        verification = "unknown"
    return {
        "place_id": str(poi.get("poi_id") or _selection_id(str(poi.get("name") or "unknown"))),
        "name": str(poi.get("name") or "未命名地点"),
        "lat": poi.get("lat"), "lng": poi.get("lng"),
        "address": poi.get("address"),
        "facts": {
            "opening_hours": {
                "open_time": poi.get("open_time"),
                "close_time": poi.get("close_time"),
                "closed_weekdays": poi.get("closed_weekdays") or [],
            },
            "ticket_price_cny": poi.get("ticket_cny"),
            "verification": verification,
            "source": source,
            "observed_at": poi.get("observed_at"),
        },
    }


def _selections(brief: dict, poi_refs: dict[str, tuple[str, str]]) -> list[dict]:
    by_name = {name.casefold(): place_id for place_id, (name, _item) in poi_refs.items()}
    result: dict[str, dict] = {}
    for values, preference in (
        (brief.get("must_go") or [], "must_visit"),
        (brief.get("optional_go") or [], "interested"),
        (brief.get("avoid") or [], "excluded"),
    ):
        for raw_name in values:
            name = str(raw_name).strip()
            if not name:
                continue
            place_id = by_name.get(name.casefold()) or _selection_id(name)
            result[place_id] = {
                "place_id": place_id, "name": name, "preference": preference,
            }
    return list(result.values())


def _previous(value: TripDocumentV2 | dict | None) -> TripDocumentV2 | None:
    if value is None:
        return None
    return value if isinstance(value, TripDocumentV2) else TripDocumentV2.model_validate(value)


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("Agent itinerary nodes must be mappings or Pydantic models")


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _activity_type(kind: Any) -> str:
    value = str(kind or "").lower()
    if value in {"meal", "dining", "food"}:
        return "meal"
    if value in {"rest", "break"}:
        return "rest"
    if value in {"shopping", "shop"}:
        return "shopping"
    if value in {"free_time", "free"}:
        return "free_time"
    return "custom"


def _leg_mode(value: Any) -> str | None:
    mode = str(value or "").strip().lower()
    aliases = {
        "walk": "walk", "walking": "walk", "步行": "walk",
        "drive": "drive", "driving": "drive", "自驾": "drive",
        "transit": "transit", "bus": "transit", "subway": "transit",
        "public_transit": "transit", "公交": "transit", "地铁": "transit",
        "taxi": "taxi", "ride": "taxi", "打车": "taxi", "出租车": "taxi",
    }
    return aliases.get(mode)


def _selection_id(name: str) -> str:
    return f"selection-{sha256(name.casefold().encode('utf-8')).hexdigest()[:24]}"


def _stable_id(value: str) -> str:
    return str(uuid5(_ITEM_NAMESPACE, value))
