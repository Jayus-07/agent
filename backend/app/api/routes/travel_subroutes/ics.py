"""Travel 行程导出为 RFC 5545 iCalendar (ICS) 文件。"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request
from fastapi.responses import Response

from backend.app.api.identity import require_identity
from .base import facade
from .schemas import IcsExportRequest

_ICS_TIME_FMT = "%Y%m%dT%H%M%S"
# 行程时刻按东八区声明（验收 #115）：此前是 floating time（无 TZID），
# 国内日历碰巧正确，但跨时区导入会按日历软件本地时区漂移。VTIMEZONE 块
# 声明 Asia/Shanghai 固定 +08:00（无夏令时），DTSTART/DTEND 带 TZID 引用。
_ICS_TZID = "Asia/Shanghai"
_ICS_VTIMEZONE_LINES = (
    "BEGIN:VTIMEZONE",
    f"TZID:{_ICS_TZID}",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0800",
    "TZOFFSETTO:+0800",
    "TZNAME:CST",
    "END:STANDARD",
    "END:VTIMEZONE",
)


def _ics_escape(text: str) -> str:
    """RFC 5545 转义：逗号/分号/反斜杠/换行。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\", "\\\\").replace(";", r"\;") \
        .replace(",", r"\,").replace("\n", r"\n")


def _dt_stamp(day_date: date, hhmm: str, fallback: str) -> str:
    try:
        h, m = int(hhmm[:2]), int(hhmm[3:5])
        s = int(hhmm[6:8]) if len(hhmm) >= 8 else 0
    except (TypeError, ValueError, IndexError):
        try:
            compact = fallback.replace(":", "")
            h, m = int(compact[:2]), int(compact[2:4])
            s = int(compact[4:6]) if len(compact) >= 6 else 0
        except (AttributeError, TypeError, ValueError, IndexError):
            h, m, s = 8, 30, 0
    return day_date.strftime("%Y%m%d") + f"T{h:02d}{m:02d}{s:02d}"


def _ics_fold_lines(lines: list[str]) -> list[str]:
    """按 RFC 5545 将内容行折叠到每行最多 75 个 UTF-8 字节。"""
    result: list[str] = []
    for line in lines:
        current = ""
        width = 0
        for char in line:
            char_width = len(char.encode("utf-8"))
            limit = 75 if not current else 74
            if width + char_width > limit:
                result.append(current)
                current, width = " " + char, 1 + char_width
            else:
                current += char
                width += char_width
        result.append(current)
    return result


def _ics_offset(value: timedelta) -> str:
    total_seconds = int(value.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    suffix = f"{hours:02d}{minutes:02d}"
    return f"{sign}{suffix}{seconds:02d}" if seconds else f"{sign}{suffix}"


def _vtimezone_lines(timezone_name: str, zone: ZoneInfo,
                     event_dates: list[date]) -> tuple[str, ...]:
    if timezone_name == _ICS_TZID:
        return _ICS_VTIMEZONE_LINES

    first_year = min((value.year for value in event_dates), default=date.today().year) - 1
    last_year = max((value.year for value in event_dates), default=date.today().year) + 1
    scan_start = datetime(first_year, 1, 1, tzinfo=datetime_timezone.utc)
    scan_end = datetime(last_year + 1, 1, 1, tzinfo=datetime_timezone.utc)
    transitions: list[tuple[datetime, timedelta, timedelta, str]] = []
    previous = scan_start.astimezone(zone).utcoffset() or timedelta(0)
    cursor = scan_start + timedelta(days=1)
    while cursor <= scan_end:
        current = cursor.astimezone(zone).utcoffset() or timedelta(0)
        if current != previous:
            low, high = cursor - timedelta(days=1), cursor
            while (high - low).total_seconds() > 1:
                middle = low + (high - low) / 2
                if (middle.astimezone(zone).utcoffset() or timedelta(0)) == previous:
                    low = middle
                else:
                    high = middle
            before = (high - timedelta(seconds=1)).astimezone(zone)
            after = high.astimezone(zone)
            onset = before.replace(tzinfo=None) + timedelta(seconds=1)
            transitions.append((onset, previous, current, after.tzname() or timezone_name))
            previous = current
        cursor += timedelta(days=1)

    lines = ["BEGIN:VTIMEZONE", f"TZID:{timezone_name}"]
    if not transitions:
        anchor = datetime(first_year, 1, 1, tzinfo=datetime_timezone.utc).astimezone(zone)
        offset = anchor.utcoffset() or timedelta(0)
        lines.extend((
            "BEGIN:STANDARD", f"DTSTART:{first_year}0101T000000",
            f"TZOFFSETFROM:{_ics_offset(offset)}",
            f"TZOFFSETTO:{_ics_offset(offset)}",
            f"TZNAME:{anchor.tzname() or timezone_name}", "END:STANDARD",
        ))
    else:
        for onset, offset_from, offset_to, name in transitions:
            component = "DAYLIGHT" if offset_to > offset_from else "STANDARD"
            lines.extend((
                f"BEGIN:{component}", f"DTSTART:{onset:%Y%m%dT%H%M%S}",
                f"TZOFFSETFROM:{_ics_offset(offset_from)}",
                f"TZOFFSETTO:{_ics_offset(offset_to)}", f"TZNAME:{name}",
                f"END:{component}",
            ))
    lines.append("END:VTIMEZONE")
    return tuple(lines)


def itinerary_to_ics(
    itinerary: dict,
    *,
    timezone_name: str | None = None,
    utc_dtstamp: bool = False,
) -> str:
    """Itinerary dict → ICS (RFC 5545) 文本（纯函数，可单测）。

    无 start_date 的行程以「今天」为第 1 天生成占位日程，并在描述里注明；
    ICS 是给日历软件消费的，缺日期时给占位比空文件更可用。
    """
    brief = itinerary.get("brief") or {}
    destination = brief.get("destination", "行程")
    timezone_name = timezone_name or _ICS_TZID
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    start_raw = brief.get("start_date")
    # model_dump 后 start_date 是 date 对象；跨 JSON 传输后是 ISO 字符串 —— 两种都要吃
    if isinstance(start_raw, date):
        base_date = start_raw
    elif isinstance(start_raw, str) and start_raw:
        try:
            base_date = date.fromisoformat(start_raw)
        except ValueError:
            base_date = date.today()
    else:
        base_date = date.today()

    day_dates: list[date] = []
    for day_idx, day in enumerate(itinerary.get("days", []), start=1):
        raw_index = day.get("day_index", day_idx)
        try:
            resolved_index = int(raw_index)
        except (TypeError, ValueError):
            resolved_index = day_idx
        raw_date = day.get("day_date")
        if isinstance(raw_date, date):
            day_dates.append(raw_date)
        elif isinstance(raw_date, str) and raw_date:
            try:
                day_dates.append(date.fromisoformat(raw_date))
            except ValueError:
                day_dates.append(base_date + timedelta(days=resolved_index - 1))
        else:
            day_dates.append(base_date + timedelta(days=resolved_index - 1))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//agent//travel-plan//CN",
        "CALSCALE:GREGORIAN",
        *_vtimezone_lines(timezone_name, zone, day_dates),
    ]
    for day_position, day in enumerate(itinerary.get("days", []), start=1):
        day_idx_raw = day.get("day_index", 1)
        try:
            day_idx = int(day_idx_raw)
        except (TypeError, ValueError):
            day_idx = 1
        day_date = day_dates[day_position - 1]
        for item in day.get("items", []):
            title = item.get("title", "安排")
            uid = item.get("uid")
            start = _dt_stamp(day_date, item.get("start", "08:30"), "083000")
            raw_end_date = item.get("end_date")
            if isinstance(raw_end_date, date):
                end_date = raw_end_date
            elif isinstance(raw_end_date, str) and raw_end_date:
                end_date = date.fromisoformat(raw_end_date)
            else:
                end_date = day_date
            end = _dt_stamp(end_date, item.get("end", "09:30"), "093000")
            note = item.get("note", "")
            desc_parts = [destination, f"第 {day.get('day_index', 1)} 天"]
            if note:
                desc_parts.append(note)
            event_uid = uid or (
                f"travel-{day.get('day_index', 1)}-{_ics_escape(title)}-{start}@agent.local"
            )
            event_lines = [
                "BEGIN:VEVENT",
                f"UID:{event_uid}",
                (
                    f"DTSTAMP:{datetime.now(datetime_timezone.utc):%Y%m%dT%H%M%SZ}"
                    if utc_dtstamp
                    else f"DTSTAMP:{start}"
                ),
            ]
            if item.get("all_day"):
                raw_all_day_end = item.get("all_day_end_date")
                if isinstance(raw_all_day_end, date):
                    all_day_end = raw_all_day_end
                elif isinstance(raw_all_day_end, str) and raw_all_day_end:
                    all_day_end = date.fromisoformat(raw_all_day_end)
                else:
                    all_day_end = day_date + timedelta(days=1)
                event_lines.extend((
                    f"DTSTART;VALUE=DATE:{day_date:%Y%m%d}",
                    f"DTEND;VALUE=DATE:{all_day_end:%Y%m%d}",
                ))
            else:
                event_lines.extend((
                    f"DTSTART;TZID={timezone_name}:{start}",
                    f"DTEND;TZID={timezone_name}:{end}",
                ))
            event_lines.extend((
                f"SUMMARY:{_ics_escape(title)}",
                f"DESCRIPTION:{_ics_escape('；'.join(desc_parts))}",
            ))
            if item.get("location"):
                event_lines.append(f"LOCATION:{_ics_escape(item['location'])}")
            event_lines.append("END:VEVENT")
            lines.extend(event_lines)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold_lines(lines)) + "\r\n"


def ics_content_disposition(destination: str) -> str:
    """构造 RFC 6266 合规的 Content-Disposition。

    HTTP 头只能 latin-1 编码，中文目的地直接内嵌 filename 会 500（D1）。
    ASCII fallback 用固定名，真实文件名经 filename*=UTF-8'' 百分号编码携带。
    """
    encoded = quote(destination, safe="")
    return f"attachment; filename=\"travel-plan.ics\"; filename*=UTF-8''{encoded}.ics"


async def travel_export_ics(request: Request):
    facade.require_identity(request)
    # 手动解析（对齐 chat_stream 先例，规避 FastAPI 中文 payload 自动解析 bug）
    try:
        req = IcsExportRequest(**(await request.json()))
    except Exception as e:
        return Response(
            json.dumps({"error": "invalid_itinerary", "message": str(e)},
                       ensure_ascii=False),
            status_code=422, media_type="application/json",
        )
    from backend.travel.models.itinerary import Itinerary

    try:
        Itinerary.model_validate(req.itinerary)
    except Exception as e:
        return Response(
            json.dumps({"error": "invalid_itinerary", "message": str(e)},
                       ensure_ascii=False),
            status_code=422, media_type="application/json",
        )
    ics_text = facade.itinerary_to_ics(req.itinerary)
    filename = (req.itinerary.get("brief") or {}).get("destination", "trip")
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": facade.ics_content_disposition(filename)},
    )


facade.register("_ICS_TIME_FMT", _ICS_TIME_FMT)
facade.register("_ICS_TZID", _ICS_TZID)
facade.register("_ICS_VTIMEZONE_LINES", _ICS_VTIMEZONE_LINES)
facade.register("_ics_escape", _ics_escape)
facade.register("_dt_stamp", _dt_stamp)
facade.register("_ics_fold_lines", _ics_fold_lines)
facade.register("_ics_offset", _ics_offset)
facade.register("_vtimezone_lines", _vtimezone_lines)
facade.register("itinerary_to_ics", itinerary_to_ics)
facade.register("ics_content_disposition", ics_content_disposition)
facade.register("travel_export_ics", travel_export_ics)
