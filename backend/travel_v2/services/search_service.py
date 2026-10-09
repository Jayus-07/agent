from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from backend.travel.agents.planning_agent import PlanningAgent
from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.services import live_search_service
from backend.travel.services.live_search_service import LiveSearchError
from backend.travel_v2.models.search import TravelSearchResponse

_STATUS_MESSAGES = {
    "business_failure": "数据源未能完成查询，请检查条件后重试。",
    "network_timeout": "查询超时，请稍后重试。",
    "provider_unavailable": "数据源当前不可用，请稍后重试。",
    "disabled": "动车查询当前未启用，未展示模拟车次。",
}


def failed_search(kind: str, exc: Exception) -> dict[str, Any]:
    """把适配器边界的异常归一为可见状态，不混同无结果。"""
    message = str(exc).lower()
    class_name = type(exc).__name__.lower()
    if "timeout" in class_name or "timeout" in message or "超时" in message:
        status = "network_timeout"
    elif "disabled" in message or "未启用" in message:
        status = "disabled"
    elif any(token in message for token in (
        "unavailable", "不可用", "未配置", "限流", "配额",
    )):
        status = "provider_unavailable"
    else:
        status = "business_failure"
    source = "12306" if kind == "train" else "tencent:lbs" if kind == "places" else "amap"
    status_message = (
        "地点查询当前未启用，未返回模拟候选。"
        if kind == "places" and status == "disabled"
        else _STATUS_MESSAGES[status]
    )
    disclosure = (
        "12306 查询来自非官方聚合源，结果可能延迟；查询失败时不会展示模拟车次。"
        if kind == "train"
        else "腾讯地点检索失败；未返回未经核实的地点候选。" if kind == "places"
        else "高德商户数据不包含酒店房态及每晚房价。" if kind == "hotel" else ""
    )
    return TravelSearchResponse(
        kind=kind, status=status, source=source, queried_at=_now(),
        message=status_message, results=[], disclosure=disclosure,
    ).model_dump(mode="json")


def _now() -> datetime:
    return datetime.now().astimezone()


def stable_selection_id(kind: str, source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{source}\0{identity}".encode("utf-8")).hexdigest()[:32]
    return f"{source}:{digest}"


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merchant_result(kind: str, merchant: dict[str, Any], queried_at: datetime) -> dict[str, Any] | None:
    name = str(merchant.get("name") or "").strip()
    if not name:
        return None
    source = str(merchant.get("source") or "amap")
    merchant_id = str(merchant.get("id") or merchant.get("merchant_id") or "").strip()
    address = str(merchant.get("address") or "").strip() or None
    identity = merchant_id or "|".join((name, address or "", str(merchant.get("lat") or ""), str(merchant.get("lng") or "")))
    result = {
        "selection_id": stable_selection_id(kind, source, identity),
        "merchant_id": merchant_id or identity,
        "name": name,
        "address": address,
        "rating": _float_or_none(merchant.get("rating")),
        "open_status": merchant.get("open_status"),
        "open_time_today": merchant.get("open_time_today"),
        "lat": _float_or_none(merchant.get("lat")),
        "lng": _float_or_none(merchant.get("lng")),
        "avg_cost_cny": _float_or_none(merchant.get("avg_cost_cny")),
        "source": source,
        "queried_at": merchant.get("updated_at") or queried_at.isoformat(),
    }
    if kind == "hotel":
        result.update({"nightly_price_cny": None, "room_availability": "unknown"})
    return result


def _merchant_response(kind: str, city: str) -> dict[str, Any]:
    queried_at = _now()
    agent = ResearchAgent()
    try:
        data = agent.search_food(city) if kind == "food" else agent.search_hotels(city)
    except LiveSearchError as exc:
        status = exc.category if exc.category in _STATUS_MESSAGES else "business_failure"
        return TravelSearchResponse(
            kind=kind, status=status, source="amap", queried_at=queried_at,
            message=_STATUS_MESSAGES[status], results=[],
            disclosure=("高德商户信息不包含房态和每晚房价。" if kind == "hotel" else ""),
        ).model_dump(mode="json")

    results = [
        item for merchant in (data.get("merchants") or [])
        if isinstance(merchant, dict)
        if (item := _merchant_result(kind, merchant, queried_at)) is not None
    ]
    status = "success" if results else "no_results"
    message = "查询完成。" if results else "暂无符合条件的商户。"
    return TravelSearchResponse(
        kind=kind, status=status, source="amap", queried_at=queried_at,
        message=message, results=results,
        disclosure=("高德返回的是商户信息，不含房态及每晚价格；房价待核实。" if kind == "hotel" else "商户评分、营业状态和位置来自高德，可能随时间变化。"),
    ).model_dump(mode="json")


def search_food(city: str) -> dict[str, Any]:
    return _merchant_response("food", city)


def search_hotels(city: str) -> dict[str, Any]:
    return _merchant_response("hotel", city)


def search_places(*, city: str, keyword: str) -> dict[str, Any]:
    """复用旅游域已有的腾讯 POI Tool；只返回真实地点候选及其来源。"""
    queried_at = _now()
    data = live_search_service.search_places(
        keyword=keyword, city=city, page_size=20,
    )
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for poi in data.get("pois") or []:
        if not isinstance(poi, dict):
            continue
        name = str(poi.get("name") or "").strip()
        if not name:
            continue
        raw_id = str(poi.get("id") or "").strip()
        lat = _float_or_none(poi.get("lat"))
        lng = _float_or_none(poi.get("lng"))
        if (lat is None) != (lng is None):
            lat = lng = None
        address = str(poi.get("address") or "").strip() or None
        identity = raw_id or "|".join((name, address or "", str(lat), str(lng)))
        if identity in seen:
            continue
        seen.add(identity)
        place_id = raw_id or stable_selection_id("poi-identity", "tencent:lbs", identity)
        results.append({
            "selection_id": stable_selection_id("place", "tencent:lbs", place_id),
            "place": {
                "place_id": place_id,
                "name": name,
                "lat": lat,
                "lng": lng,
                "address": address,
                "facts": {
                    "opening_hours": None,
                    "ticket_price_cny": None,
                    "verification": "unknown",
                    "source": "tencent:lbs",
                    "observed_at": queried_at.isoformat(),
                },
            },
            "category": str(poi.get("category") or "").strip() or None,
            "source": "tencent:lbs",
            "queried_at": queried_at.isoformat(),
        })
    status = "success" if results else "no_results"
    return TravelSearchResponse(
        kind="places", status=status, source="tencent:lbs",
        queried_at=queried_at,
        message="地点检索完成。" if results else "查询成功，但没有匹配地点。",
        results=results,
        disclosure="地点名称、坐标和地址来自腾讯地点检索；该 Provider 不提供已核实营业时间或门票价格。",
    ).model_dump(mode="json")


def _duration_min(train: dict[str, Any]) -> int | None:
    raw = train.get("duration_min")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    duration = str(train.get("duration") or "")
    hours = re.search(r"(\d+)\s*小时", duration)
    minutes = re.search(r"(\d+)\s*分", duration)
    if hours or minutes:
        result = int(hours.group(1) if hours else 0) * 60 + int(minutes.group(1) if minutes else 0)
        return result or None
    start = str(train.get("start_time") or "")
    end = str(train.get("arrive_time") or train.get("arrival_time") or "")
    try:
        start_min = int(start[:2]) * 60 + int(start[3:5])
        end_min = int(end[:2]) * 60 + int(end[3:5])
    except (ValueError, IndexError):
        return None
    return (end_min - start_min) % (24 * 60) or None


def search_trains(*, from_station: str, to_station: str, travel_date: str) -> dict[str, Any]:
    queried_at = _now()
    planner = PlanningAgent()
    try:
        data = planner.search_trains(
            from_station=from_station, to_station=to_station,
            travel_date=travel_date, limit=20,
        )
        if data.get("trains"):
            data = planner.attach_train_prices(
                data, from_station=from_station, to_station=to_station,
                travel_date=travel_date, limit=2,
            )
    except LiveSearchError as exc:
        status = exc.category if exc.category in _STATUS_MESSAGES else "business_failure"
        return TravelSearchResponse(
            kind="train", status=status, source="12306", queried_at=queried_at,
            message=_STATUS_MESSAGES[status], results=[],
            disclosure="12306 查询来自非官方聚合源，未启用或失败时不会展示模拟车次。",
        ).model_dump(mode="json")

    results: list[dict[str, Any]] = []
    for train in data.get("trains") or []:
        if not isinstance(train, dict):
            continue
        train_code = str(train.get("train_no") or train.get("train_code") or "").strip()
        departure_time = str(train.get("start_time") or train.get("departure_time") or "").strip()
        arrival_time = str(train.get("arrive_time") or train.get("arrival_time") or "").strip()
        if not train_code or not departure_time or not arrival_time:
            continue
        departure_station = str(train.get("from_station") or data.get("from_station") or from_station)
        arrival_station = str(train.get("to_station") or data.get("to_station") or to_station)
        identity = "|".join((travel_date, departure_station, arrival_station, train_code))
        fares = train.get("prices") if isinstance(train.get("prices"), dict) else {}
        tickets = train.get("seats") if isinstance(train.get("seats"), dict) else {}
        results.append({
            "selection_id": stable_selection_id("train", "12306", identity),
            "train_code": train_code,
            "travel_date": str(data.get("date") or travel_date),
            "departure_station": departure_station,
            "arrival_station": arrival_station,
            "departure_time": departure_time,
            "arrival_time": arrival_time,
            "duration_min": _duration_min(train),
            "duration": train.get("duration") or "",
            "tickets": tickets,
            "fares": fares,
            "ticket_source": str(train.get("source") or data.get("source") or "12306"),
            "fare_source": "12306" if fares else None,
            "queried_at": train.get("updated_at") or data.get("queried_at") or queried_at.isoformat(),
            "verification": "delayed",
        })
    status = "success" if results else "no_results"
    return TravelSearchResponse(
        kind="train", status=status, source="12306", queried_at=queried_at,
        message="查询完成，结果可能延迟。" if results else "该线路与日期暂无车次。",
        results=results,
        disclosure="车次和余票来自 12306 非官方聚合源，可能延迟；票价只显示上游明确返回的金额，出行前请以 12306 官方为准。",
    ).model_dump(mode="json")
