"""travel/services/weather_service.py — 天气服务（Phase 3 Commit A）

真身自 experts/weather.py 逐字迁入。职责分两段：
  - 取数与解析（Research 面）：fetch_forecast_evidence（**V2 收敛点**——
    Provider facade 直连自 expert 迁入本 service；Phase 4 起经
    ProviderRouter 链账装配取数，并组装 Evidence 三元组；fetch_forecast
    保留为兼容 wrapper）/ is_bad_weather / bad_weather_dates
  - 坏天气适应（Optimization 面）：plan_weather_swaps（户外→室内替换，
    必去永不换；重排复用 transit_service.rebuild_days 唯一实现）

三条边界（原样保留）：必去项永不替换；未提供出发日期 / 天气 API 失败 →
跳过检查并留下说明，不阻塞主链；换入候选必须来自既有候选池。
"""
from __future__ import annotations

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.core.contracts import SourceType
from backend.travel.core.evidence_utils import evidence_to_dict, make_evidence, parse_iso
from backend.travel.models.poi import (
    CATEGORY_MEAL,
    CATEGORY_NIGHT,
    CATEGORY_PARK,
    CATEGORY_SHOPPING,
    Poi,
)
from backend.travel.services.transit_service import rebuild_days
from backend.tools.travel.cost import estimate_cost

# 户外判据：类别为公园，或带「自然」标签的景点（登山/湖边/海边遇雨体验骤降）
_OUTDOOR_TAG = "自然"
_OUTDOOR_CATEGORIES = {CATEGORY_PARK}
# 室内候选判据：非户外类别，且不带「自然」标签
_INDOOR_CATEGORIES = {CATEGORY_MEAL, CATEGORY_NIGHT, CATEGORY_SHOPPING}


def _cat_and_tags(obj) -> tuple[str, list[str]]:
    """Poi 或 dict（候选池存储形态）→ (category, tags)。

    候选池在 state 里是 dict（可 JSON 序列化纪律），行程里是 Poi ——
    判定函数必须两种形态都吃，否则节点与纯函数各自转一遍必出错。
    """
    if isinstance(obj, dict):
        return obj.get("category", ""), list(obj.get("tags") or [])
    return obj.category, list(obj.tags or [])


def _is_outdoor(poi) -> bool:
    category, tags = _cat_and_tags(poi)
    return category in _OUTDOOR_CATEGORIES or _OUTDOOR_TAG in tags


def _is_indoor(poi) -> bool:
    category, tags = _cat_and_tags(poi)
    return (category in _INDOOR_CATEGORIES
            or (_OUTDOOR_TAG not in tags and category not in _OUTDOOR_CATEGORIES))


def is_bad_weather(weather_text: str) -> bool:
    """预报文本 → 是否坏天气（纯函数，可单测）。

    含「小雨」（分级后的提示级）——「是否坏天气」与「是否触发替换」是
    两个口径：披露/证据面用本函数，替换面用 :func:`weather_severity`。
    """
    text = weather_text or ""
    return any(k in text for k in T.TRAVEL_BAD_WEATHER_KEYWORDS)


def weather_severity(weather_text: str) -> str:
    """预报文本 → 强度分级："none" | "mild" | "severe"（纯函数，可单测）。

    - mild：仅「小雨」——提示带伞，不触发户外→室内替换（小雨出游体验
      损失有限，替换的扰动反而更大）；
    - severe：命中坏天气词表、或 mild 文本混有强天气词（"小雨转大雨"
      必须按大雨处理，防降级误放）。
    """
    text = weather_text or ""
    if any(k in text for k in T.TRAVEL_WEATHER_SEVERE_OVERRIDE_KEYWORDS):
        return "severe"
    if any(k in text for k in T.TRAVEL_WEATHER_MILD_KEYWORDS):
        return "mild"
    return "severe" if is_bad_weather(text) else "none"


def fetch_forecast_evidence(destination: str) -> tuple[dict | None, str, dict | None]:
    """查未来几天预报 + 组装天气 Evidence（Phase 4 三元组通道）。

    fetch_forecast 的证据增强版：预报与七态降级说明语义逐字保留，第三位
    返回 Evidence dict（取不到数据时为 None）。Evidence 在 service 层组装
    （provider 层零改动）：status×Freshness→LIVE/CACHE，stale 降 0.6，
    expire_at=observed_at+TTL(weather)（travel-domain-design-v5.md §13「现有 TTL 即 expire_at」）；
    降级链的实际服务源由 result.provider 自带——qweather 接管后信任口径
    自动反映（travel-domain-design-v5.md §15「每次降级记 Evidence」）。

    Returns:
        (预报 dict 或 None, 降级说明, Evidence dict 或 None)
    """
    try:
        from backend.providers.travel.live import get_weather_provider
        from backend.providers.travel.live.result import (
            Freshness,
            ProviderStatus,
        )
        from backend.providers.travel.live.router import ttl_for

        result = get_weather_provider().forecast_payload(destination)
        if result.ok:
            from datetime import timedelta

            evidence = evidence_to_dict(make_evidence(
                f"weather:{destination}",
                value={"city": destination,
                       "days": len((result.data or {}).get("days", [])),
                       "served_by": result.provider},
                source=result.provider,
                source_type=(SourceType.CACHE
                             if result.freshness in (Freshness.CACHED,
                                                      Freshness.STALE)
                             else SourceType.LIVE),
                verified_at=parse_iso(result.observed_at),
                expire_at=(parse_iso(result.observed_at)
                           + timedelta(seconds=ttl_for("weather"))
                           ) if parse_iso(result.observed_at) else None,
                confidence=(0.6
                            if result.freshness == Freshness.STALE else None),
            ))
            return result.data, "", evidence
        if result.status == ProviderStatus.DISABLED:
            return None, "", None
        if result.status == ProviderStatus.TIMEOUT:
            return None, "天气服务响应超时", None
        if result.status == ProviderStatus.RATE_LIMITED:
            return None, "天气服务配额已达软预算", None
        if result.status == ProviderStatus.NOT_FOUND:
            return None, "未获取到该城市的预报数据", None
        return None, "天气服务暂时不可用", None
    except Exception as e:  # noqa: BLE001 — 天气失败软降级
        logger.warning("[TravelWeather] 天气查询失败（跳过检查）: %s", e)
        return None, "天气服务暂时不可用", None


def itinerary_centroid(itinerary) -> tuple[float, float] | None:
    """行程全部到访景点的质心（#9b 区县级天气的查询锚点；纯函数）。

    单区县口径：多天行程取整体质心——一天一个区县会放大成 N 次逆地理+
    N 次预报调用，而天气检查是增强项，精度够用即可。
    """
    pts = [
        (i.poi.lat, i.poi.lng)
        for d in (itinerary.days if itinerary is not None else [])
        for i in d.items
        if i.kind == "visit" and i.poi is not None
    ]
    if not pts:
        return None
    return (sum(lat for lat, _ in pts) / len(pts),
            sum(lng for _, lng in pts) / len(pts))


def district_for_point(lat: float, lng: float) -> str:
    """坐标 → 区县名（腾讯逆地理，软失败→空串；#9b 区县级天气锚点）。"""
    try:
        from backend.infra.lbs import api as lbs_api

        rev = lbs_api.reverse_geocode(lat, lng)
        return ((rev or {}).get("district") or "").strip()
    except Exception:  # noqa: BLE001 — 区县解析失败回退城市级
        logger.debug("[WeatherService] 区县反查失败（回退城市级）", exc_info=True)
        return ""


def fetch_forecast(destination: str) -> tuple[dict | None, str]:
    """查未来几天预报（兼容入口：签名/语义不变——补丁缝与存量消费面）。

    证据增强通道见 fetch_forecast_evidence（Phase 4 起节点走该通道）。"""
    forecast, reason, _ = fetch_forecast_evidence(destination)
    return forecast, reason


def bad_weather_dates(forecast: dict) -> list[str]:
    """从预报结构提取**替换级（severe）**坏天气日期（"YYYY-MM-DD"）。

    future 结构：days=[{date, day:{weather,...}, night:{...}}]；
    白天与夜间任一达到 severe（含「小雨」混强天气词的情形）即判替换日；
    仅「小雨」的 mild 日不在此列（由 :func:`mild_weather_dates` 承接，
    分级口径 2026-10-04：小雨提示带伞不替换，替换的扰动比出游损失大）。
    """
    bad: list[str] = []
    for rec in (forecast or {}).get("days", []):
        day_weather = ((rec.get("day") or {}).get("weather") or "")
        night_weather = ((rec.get("night") or {}).get("weather") or "")
        if (weather_severity(day_weather) == "severe"
                or weather_severity(night_weather) == "severe"):
            if rec.get("date"):
                bad.append(rec["date"])
    return bad


def mild_weather_dates(forecast: dict) -> list[str]:
    """仅「小雨」的日期（提示带伞，不触发替换；白天为主夜间兜底）。"""
    mild: list[str] = []
    for rec in (forecast or {}).get("days", []):
        day_weather = ((rec.get("day") or {}).get("weather") or "")
        night_weather = ((rec.get("night") or {}).get("weather") or "")
        day_level = weather_severity(day_weather)
        night_level = weather_severity(night_weather)
        if "severe" not in (day_level, night_level) and "mild" in (day_level, night_level):
            if rec.get("date"):
                mild.append(rec["date"])
    return mild


def plan_weather_swaps(
    itinerary,
    candidates: list[dict],
    bad_dates: list[str],
) -> tuple[object, list[dict], list[str]]:
    """坏天气日户外→室内替换（纯函数，可单测）。

    Returns:
        (新行程或 None[无可替换], 替换动作列表, 追加提示)
    """
    used_ids = {i.poi.poi_id for d in itinerary.days for i in d.items
                if i.kind == "visit" and i.poi is not None}
    indoor_pool = [
        c for c in candidates
        if c.get("poi_id") not in used_ids and _is_indoor(c) and not c.get("required")
    ]
    # 替换优先级：热度高的室内点先上（与骨架分配同一口味）
    indoor_pool.sort(key=lambda c: (-c.get("rating", 0.0), c.get("poi_id", "")))

    actions: list[dict] = []
    extra_notes: list[str] = []
    day_spec: dict[int, list] = {}
    changed = False

    for day in itinerary.days:
        day_key = day.day_date.isoformat() if day.day_date else ""
        pois = [i.poi for i in day.items
                if i.kind == "visit" and i.poi is not None]
        if day_key not in bad_dates or not any(_is_outdoor(p) for p in pois):
            day_spec[day.day_index] = (day.day_date, pois)
            continue

        final_pois: list = []
        swapped: list[tuple[str, str]] = []
        for poi in pois:
            if _is_outdoor(poi) and not poi.required and indoor_pool:
                replacement = indoor_pool.pop(0)
                new_poi = Poi.model_validate(replacement)
                swapped.append((poi.name, new_poi.name))
                final_pois.append(new_poi)
                used_ids.add(new_poi.poi_id)
                changed = True
            else:
                final_pois.append(poi)
                if _is_outdoor(poi) and poi.required:
                    extra_notes.append(
                        f"第 {day.day_index} 天的必去点「{poi.name}」当天预报有雨，"
                        "已保留安排，请备好雨具并关注景区公告"
                    )
        if swapped:
            actions.append({
                "day_index": day.day_index,
                "date": day_key,
                "swaps": [{"from": a, "to": b} for a, b in swapped],
                "reason": "预报有雨，户外点位替换为室内候选",
            })
        day_spec[day.day_index] = (day.day_date, final_pois)

    if not changed:
        return None, actions, extra_notes

    specs = [(idx, day_spec[idx][0], day_spec[idx][1]) for idx in sorted(day_spec)]
    new_itinerary = rebuild_days(itinerary.brief, specs)
    new_itinerary.warnings = list(itinerary.warnings)
    new_itinerary.sources = list(itinerary.sources)
    new_itinerary.repair_rounds = itinerary.repair_rounds
    new_itinerary.cost = estimate_cost(new_itinerary.days,
                                       itinerary.brief.party_size,
                                       city=itinerary.brief.destination)
    # 版本事实沿用旧值：天气替换不改变 brief（非需求变化），候选池签名不变；
    # change_reason 用 repair 语义会让「调整说明」误报校验修复，故保持原值。
    new_itinerary.plan_version = itinerary.plan_version
    new_itinerary.parent_plan_version = itinerary.parent_plan_version
    new_itinerary.brief_version = itinerary.brief_version
    new_itinerary.data_snapshot_version = itinerary.data_snapshot_version
    new_itinerary.created_at = itinerary.created_at
    new_itinerary.change_reason = itinerary.change_reason
    return new_itinerary, actions, extra_notes
