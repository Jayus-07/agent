from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

from backend.app.api.routes.travel import itinerary_to_ics
from backend.travel_v2.models.trip import TripDocumentV2


def _event_uid(*public_values: str) -> str:
    identity = "\0".join(public_values)
    return f"v2-{sha256(identity.encode('utf-8')).hexdigest()[:32]}@agent.local"


def trip_document_to_ics(snapshot: dict) -> str:
    """将正式 V2 Revision 快照适配到既有 RFC 5545 ICS 转换器。"""
    document = TripDocumentV2.model_validate(snapshot).model_dump(mode="json")
    brief = document["brief"]
    zone = ZoneInfo(brief["timezone"])
    start_date = brief.get("start_date")
    legacy_days = []
    legacy_by_date: dict[str, dict] = {}

    for index, day in enumerate(document["days"], start=1):
        day_date = day.get("date") or start_date
        if not day_date:
            raise ValueError("ICS 导出需要 Revision 包含明确的本地日期")
        if isinstance(day_date, str):
            day_date = date.fromisoformat(day_date)
        legacy_items = []
        for item_index, item in enumerate(day["items"]):
            place = item.get("place") or {}
            location_values = (place.get("name"), place.get("address"))
            location = "，".join(value for value in location_values if value)
            start_time = item.get("start_time")
            uid = _event_uid(
                day_date.isoformat(),
                str(item_index),
                item["title"],
                start_time or "all-day",
                location,
            )
            if not start_time:
                legacy_items.append(
                    {
                        "title": item["title"],
                        "all_day": True,
                        "uid": uid,
                        "location": location,
                    }
                )
                continue

            local_time = time.fromisoformat(start_time)
            wall_start = datetime.combine(day_date, local_time)
            local_start = wall_start.replace(tzinfo=zone, fold=0)
            # 拒绝 DST 春季跳时中不存在的本地时刻，避免导出错误瞬间。
            round_trip = local_start.astimezone(timezone.utc).astimezone(zone)
            if round_trip.replace(tzinfo=None) != wall_start:
                raise ValueError("ICS 导出遇到时区切换中不存在的本地开始时间")
            end_instant = local_start.astimezone(timezone.utc) + timedelta(
                minutes=item["duration_min"]
            )
            local_end = end_instant.astimezone(zone)
            legacy_items.append(
                {
                    "title": item["title"],
                    "start": local_start.strftime("%H:%M:%S"),
                    "end": local_end.strftime("%H:%M:%S"),
                    "end_date": local_end.date().isoformat(),
                    "uid": uid,
                    "location": location,
                }
            )
        legacy_day = {
            "day_index": index,
            "day_date": day_date.isoformat(),
            "title": day["title"],
            "items": legacy_items,
        }
        legacy_days.append(legacy_day)
        legacy_by_date[day_date.isoformat()] = legacy_day

    def event_day(event_date: date, label: str) -> dict:
        date_key = event_date.isoformat()
        existing = legacy_by_date.get(date_key)
        if existing:
            return existing
        value = {
            "day_index": len(legacy_days) + 1,
            "day_date": date_key,
            "title": label,
            "items": [],
        }
        legacy_days.append(value)
        legacy_by_date[date_key] = value
        return value

    for lodging_index, lodging in enumerate(document["arrangements"]["lodgings"]):
        check_in = date.fromisoformat(lodging["check_in"])
        merchant = lodging["merchant"]
        location_values = (merchant["name"], merchant.get("address"))
        location = "，".join(value for value in location_values if value)
        lodging_uid = _event_uid(
            check_in.isoformat(),
            lodging["check_out"],
            str(lodging_index),
            merchant["name"],
            location,
        )
        event_day(check_in, "住宿")["items"].append(
            {
                "title": f"住宿：{merchant['name']}",
                "all_day": True,
                "all_day_end_date": lodging["check_out"],
                "uid": f"v2-lodging-{lodging_uid}@agent.local",
                "location": location,
                "note": "住宿地点参考，未预订",
            }
        )

    for train_index, train in enumerate(document["arrangements"]["intercity_trains"]):
        travel_date = date.fromisoformat(train["travel_date"])
        departure_time = time.fromisoformat(train["departure_time"])
        wall_start = datetime.combine(travel_date, departure_time)
        local_start = wall_start.replace(tzinfo=zone, fold=0)
        round_trip = local_start.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) != wall_start:
            raise ValueError("ICS 导出遇到时区切换中不存在的动车出发时间")
        end_instant = local_start.astimezone(timezone.utc) + timedelta(
            minutes=train["duration_min"]
        )
        local_end = end_instant.astimezone(zone)
        location = f"{train['departure_station']} → {train['arrival_station']}"
        train_uid = _event_uid(
            travel_date.isoformat(),
            str(train_index),
            train["train_code"],
            train["departure_station"],
            train["arrival_station"],
            train["departure_time"],
            train["arrival_time"],
        )
        event_day(travel_date, "城际交通")["items"].append(
            {
                "title": f"动车 {train['train_code']}",
                "start": local_start.strftime("%H:%M:%S"),
                "end": local_end.strftime("%H:%M:%S"),
                "end_date": local_end.date().isoformat(),
                "uid": f"v2-train-{train_uid}@agent.local",
                "location": location,
                "note": (
                    f"{train['departure_station']} {train['departure_time']} → "
                    f"{train['arrival_station']} {train['arrival_time']}，"
                    f"行程时长 {train['duration_min']} 分钟；未购票"
                ),
            }
        )

    legacy_document = {
        "brief": {
            "destination": brief["destination"],
            "start_date": start_date,
            "timezone": brief["timezone"],
        },
        "days": legacy_days,
    }
    return itinerary_to_ics(
        legacy_document,
        timezone_name=brief["timezone"],
        utc_dtstamp=True,
    )
