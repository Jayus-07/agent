"""travel/experts/weather.py — 天气专家（P0-2，2026-09-22 接入域图）

职责：出发日期已知时查目的地天气预报，坏天气日尝试把**户外 POI 换成
同城的室内候选**，换不动就如实写 warning —— 与 risk 专家同一立场：
不假装掌握实时数据，但既然天气 API 可用，就应该真的用它改变行程。

编排位置：transit 之后、budget 之前。放在 transit 后的原因：
  1. 换 POI 需要重排当日时刻表，复用 rebuild_days（与 repair 同一重排路径）；
  2. 放在 transit 前（影响骨架）需要 supervisor 更早拿到预报，而骨架分配
     与天气无关 —— 只需在「哪些点被排进哪天」之后做室内/户外替换即可。

三条边界：
  - **必去项永不替换**（与 repair 同一硬规矩）：必去的户外点遇雨只加提示；
  - **未提供出发日期 / 天气 API 失败 → 跳过检查**并留下说明，不阻塞主链；
  - 换入的候选必须来自既有候选池（candidates），不凭空造地点。

并发/超时：天气调用走腾讯 LBS 缓存（TTL 由 config/map 控制），本节点
再加整体超时预算（TRAVEL_WEATHER_TIMEOUT_S），失败软降级 —— 天气检查
绝不能成为排程链路的新故障点。
"""
from __future__ import annotations

from datetime import timedelta

from backend.config import travel as T
from backend.shared.logger import logger
from backend.travel.experts.base import run_expert_safely
from backend.travel.graph_state import load_brief, load_itinerary, save_itinerary
from backend.travel.models.poi import CATEGORY_MEAL, CATEGORY_NIGHT, CATEGORY_PARK, CATEGORY_SHOPPING

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
    """预报文本 → 是否坏天气（纯函数，可单测）。"""
    text = weather_text or ""
    return any(k in text for k in T.TRAVEL_BAD_WEATHER_KEYWORDS)


def fetch_forecast(destination: str) -> tuple[dict | None, str]:
    """查未来几天预报（STOP J5：经 Provider 层——6s 预算/共享缓存/遥测）。

    Returns:
        (预报 dict 或 None, 降级说明)；任何失败返回 (None, 原因)，
        调用方跳过检查并向用户披露。
    """
    try:
        from backend.providers.travel.live import get_weather_provider
        from backend.providers.travel.live.result import ProviderStatus

        result = get_weather_provider().forecast_payload(destination)
        if result.ok:
            return result.data, ""
        if result.status == ProviderStatus.DISABLED:
            return None, ""
        if result.status == ProviderStatus.TIMEOUT:
            return None, "天气服务响应超时"
        if result.status == ProviderStatus.RATE_LIMITED:
            return None, "天气服务配额已达软预算"
        if result.status == ProviderStatus.NOT_FOUND:
            return None, "未获取到该城市的预报数据"
        return None, "天气服务暂时不可用"
    except Exception as e:  # noqa: BLE001 — 天气失败软降级
        logger.warning("[TravelWeather] 天气查询失败（跳过检查）: %s", e)
        return None, "天气服务暂时不可用"


def bad_weather_dates(forecast: dict) -> list[str]:
    """从预报结构提取坏天气日期列表（"YYYY-MM-DD"）。

    future 结构：days=[{date, day:{weather,...}, night:{...}}]；
    白天与夜间任一命中坏天气词即判坏天气日（夜间暴雨同样影响次日体验，
    且用户多在白天活动，以白天为主、夜间兜底）。
    """
    bad: list[str] = []
    for rec in (forecast or {}).get("days", []):
        day_weather = ((rec.get("day") or {}).get("weather") or "")
        night_weather = ((rec.get("night") or {}).get("weather") or "")
        if is_bad_weather(day_weather) or is_bad_weather(night_weather):
            if rec.get("date"):
                bad.append(rec["date"])
    return bad


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
                from backend.travel.models.poi import Poi

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

    from backend.travel.experts.transit import rebuild_days
    from backend.tools.travel.cost import estimate_cost

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


def weather_expert_node(state: dict) -> dict:
    """天气专家节点：预报 → 坏天气日室内替换 / 提示。"""

    def _run(_state: dict) -> dict:
        brief = load_brief(state)
        itinerary = load_itinerary(state)

        # 前置闸：开关关 / 无行程 / 无出发日期 → 跳过（不花一次网络调用）
        if not T.TRAVEL_WEATHER_ENABLED:
            return {"status": "success", "data": {}, "notes": []}
        if itinerary is None:
            return {"status": "failed", "data": {}, "notes": [],
                    "error": "行程尚未生成，无法执行天气检查"}
        if brief.start_date is None:
            return {"status": "success", "data": {}, "notes": [
                "未提供出发日期，已跳过天气检查；提供日期后可重新规划以纳入天气因素"
            ]}

        forecast, degrade_reason = fetch_forecast(brief.destination)
        if not forecast:
            # §44：Provider down 行程仍出单，只披露；降级原因来自
            # Provider 状态分类（timeout/配额/不可用），不再笼统一句话
            note = "天气预报暂时不可用"
            if degrade_reason and degrade_reason != "天气服务暂时不可用":
                note = f"天气检查已跳过（{degrade_reason}）"
            return {"status": "success", "data": {}, "notes": [
                f"{note}，本次未做天气检查；出行前请自行确认天气"
            ]}

        # 预报窗口与行程日期求交：预报只覆盖未来几天，远期行程按日匹配，
        # 匹配不到的日期自然不触发替换。
        # §43 OUT_OF_HORIZON（STOP J5）：预报与行程日期**零交集**说明出行
        # 日期超出 Provider 可信窗口——如实披露，绝不拿今天的天气伪装未来。
        bad_dates = bad_weather_dates(forecast)
        forecast_dates = {(rec.get("date") or "") for rec in (forecast.get("days") or [])}
        trip_dates = {
            (brief.start_date + timedelta(days=i)).isoformat()
            for i in range(brief.resolved_days())
        }
        if trip_dates and forecast_dates and not (trip_dates & forecast_dates):
            horizon = len(forecast_dates)
            return {"status": "success", "data": {}, "notes": [
                f"出行日期超出天气预报的可信范围（当前预报仅覆盖未来约 {horizon} 天），"
                "本次未做天气检查；临近出发时可让我重新评估"
            ]}
        hit_dates = sorted(set(bad_dates) & trip_dates)
        if not hit_dates:
            return {"status": "success", "data": {}, "notes": []}

        new_itinerary, actions, extra_notes = plan_weather_swaps(
            itinerary, _state.get("candidates", []), hit_dates,
        )
        notes = list(extra_notes)
        if not new_itinerary:
            if actions:  # 理论不达：有动作却没有产物，防御性兜底
                return {"status": "success", "data": {}, "notes": notes}
            notes.append(
                "出行期间预报有雨，但候选池中没有可替换的室内地点；"
                "行程维持原样，请备好雨具"
            )
            return {"status": "success", "data": {}, "notes": notes}

        logger.info("[TravelWeather] 坏天气日 %s，替换动作 %d 项",
                    hit_dates, len(actions))
        return {"status": "success",
                "data": {"itinerary": save_itinerary(new_itinerary),
                         "weather_actions": actions},
                "notes": notes}

    result = run_expert_safely("weather", _run, state)
    data = result.get("data") or {}

    history = list(state.get("expert_history", []))
    history.append({"expert": "weather", "status": result.get("status", "failed"),
                    "duration_ms": result.get("duration_ms", 0)})

    update: dict = {
        "last_expert_result": dict(result),
        "expert_history": history,
        "notes": list(state.get("notes", [])) + list(result.get("notes", [])),
    }
    if data.get("itinerary"):
        update["itinerary"] = data["itinerary"]
    # 天气替换动作进 repair_log 展示通道：reporter 的「行程自动调整说明」
    # 会把 swapped 转成可读文案，用户能看到「为什么第 2 天变了」。
    weather_actions = data.get("weather_actions") or []
    if weather_actions:
        log = list(state.get("repair_log", []))
        for action in weather_actions:
            log.append({
                "code": "weather_swap",
                "day_index": action.get("day_index", 0),
                "dropped": [s["from"] for s in action.get("swaps", [])],
                "kept_required": [],
                "reason": action.get("reason", ""),
                "weather_swaps": action.get("swaps", []),
            })
        update["repair_log"] = log
    return update
