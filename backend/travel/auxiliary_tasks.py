"""旅游域主规划之外的受限实时查询任务节点。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from backend.core.tool_runtime.models import ToolCriticality, ToolResult, ToolStatus
from backend.shared.logger import logger
from backend.travel.core.intent import TravelAdditionalTask
from backend.travel.services.live_search_service import LiveSearchError
from backend.travel.services.tool_failure_policy import (
    PROVIDER_12306,
    PROVIDER_AMAP,
    PROVIDER_QWEATHER,
    realtime_disclosure,
    run_checked,
    state_records,
)

_MAX_TASKS_PER_TURN = 4


def _task_record(
    task: TravelAdditionalTask,
    *,
    status: str,
    data_status: str,
    result_count: int = 0,
    data: dict | None = None,
    duration_ms: int = 0,
    message: str = "",
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "type": task.type,
        "params": task.params.model_dump(mode="json"),
        "status": status,
        "data_status": data_status,
        "result_count": result_count,
        "data": data or {},
        "duration_ms": duration_ms,
        "message": message,
        "missing_fields": missing_fields or [],
    }


def _append_policy_records(
    update: dict[str, Any],
    state: dict[str, Any],
    tool: str,
    provider: str,
    result: ToolResult,
) -> None:
    policy = state_records(
        tool, provider, result,
        note=realtime_disclosure(provider),
    )
    for key in ("degraded_tools", "blocked_tools", "tool_failures"):
        update[key] = [*(update.get(key) or state.get(key) or []), *policy[key]]
    if policy["degraded_tools"]:
        update["notes"].append(policy["degraded_tools"][0]["note"])


def _run_checked_task(
    task: TravelAdditionalTask,
    *,
    tool: str,
    agent: str,
    provider: str,
    call: Callable[[], dict],
    summarize: Callable[[dict], dict],
) -> tuple[dict | None, ToolResult, dict]:
    data, outcome = run_checked(
        tool,
        agent,
        call,
        provider=provider,
        dependency=ToolCriticality.OPTIONAL,
        task_id=task.task_id,
        result_summary=summarize,
    )
    summary = summarize(data) if data is not None else {}
    status = (
        "success" if outcome.status is ToolStatus.SUCCESS else "degraded")
    record = _task_record(
        task,
        status=status,
        data_status=str(summary.get("data_status") or (
            "available" if data is not None else "unavailable")),
        result_count=int(summary.get("result_count") or 0),
        # 只把确定性摘要存入 checkpoint，原始 Provider 响应不跨节点传播。
        data=summary,
        duration_ms=outcome.latency_ms,
        message=outcome.degraded_reason or "",
    )
    return data, outcome, record


def _execute_train(
    task: TravelAdditionalTask,
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict:
    params = task.params
    origin = params.origin.strip()
    destination = params.destination.strip()
    if not origin or not destination:
        status = "missing_origin" if not origin else "missing_destination"
        update["transit_query"] = {
            "status": status, "origin": origin, "destination": destination,
        }
        missing = [name for name, value in (
            ("origin", origin), ("destination", destination),
        ) if not value]
        return _task_record(
            task, status="needs_clarification", data_status="missing_inputs",
            message="查车票需要出发城市和到达城市。",
            missing_fields=missing,
        )

    travel_date = params.travel_date.strip()
    date_defaulted = not travel_date
    if date_defaulted:
        travel_date = (date.today() + timedelta(days=1)).isoformat()
        update["notes"].append(
            f"未说出发日期，车票按明天（{travel_date}）查询；"
            "需要其他日期可直接告诉我。"
        )
    try:
        date.fromisoformat(travel_date)
    except ValueError:
        update["transit_query"] = {
            "status": "failed", "origin": origin,
            "destination": destination, "date": travel_date,
        }
        return _task_record(
            task, status="needs_clarification", data_status="invalid_input",
            message="车票日期格式无法确认，请重新说出发日期。",
            missing_fields=["travel_date"],
        )

    from backend.travel.services import live_search_service

    def _summary(data: dict) -> dict:
        return live_search_service.train_preview(data)

    data, outcome, record = _run_checked_task(
        task,
        tool="travel_train_search_tool",
        agent="planning",
        provider=PROVIDER_12306,
        call=lambda: live_search_service.search_trains(
            from_station=origin,
            to_station=destination,
            travel_date=travel_date,
            limit=6,
        ),
        summarize=_summary,
    )
    update["transit_query"] = {
        "status": "ok" if data is not None else "failed",
        "origin": origin,
        "destination": destination,
        "date": travel_date,
        **(data or {}),
    }
    if date_defaulted and record["status"] == "success":
        record["message"] = "未指定日期，已按明天查询。"
    if data is not None:
        _annotate_return_connection(task, state, update, record, data)
    if data is None:
        _append_policy_records(
            update, state, "travel_train_search_tool", PROVIDER_12306, outcome)
    return record


def _annotate_return_connection(
    task: TravelAdditionalTask,
    state: dict[str, Any],
    update: dict[str, Any],
    record: dict[str, Any],
    data: dict[str, Any],
) -> None:
    """返程衔接校验（收编重构 U2，Intercity Scout）：查询日 == 行程末日时，
    比对「行程末日最后一项的结束时刻」与「车次发车时刻」，给出确定性衔接
    标注——不拦截不改单，只让用户看到哪几班车赶得上。"""
    brief = state.get("brief") or {}
    itinerary = state.get("itinerary") or {}
    if not isinstance(brief, dict) or not isinstance(itinerary, dict):
        return
    start = brief.get("start_date") or ""
    days = int(brief.get("days") or 0)
    if not start or days <= 0:
        return
    try:
        last_day = (date.fromisoformat(str(start)[:10])
                    + timedelta(days=days - 1)).isoformat()
    except ValueError:
        return
    travel_date = str(data.get("date") or task.params.travel_date or "")[:10]
    if travel_date != last_day:
        return
    last_day_items = ((itinerary.get("days") or [{}])[-1]
                      .get("items") if isinstance(itinerary.get("days"), list) else None) or []
    latest_end = ""
    for item in last_day_items:
        if not isinstance(item, dict):
            continue
        end = str(item.get("end_time") or item.get("start_time") or "")
        if end > latest_end:
            latest_end = end
    if not latest_end:
        return
    trains = data.get("trains") or []
    feasible, infeasible = [], []
    for train in trains:
        if not isinstance(train, dict):
            continue
        departure = str(train.get("start_time") or train.get("departure_time") or "")
        if not departure:
            continue
        (feasible if departure >= latest_end else infeasible).append(
            str(train.get("train_no") or train.get("train_code") or ""))
    if feasible or infeasible:
        record["connection_check"] = {
            "last_day": last_day,
            "latest_item_end": latest_end,
            "feasible_trains": feasible,
            "infeasible_trains": infeasible,
        }
        if infeasible and not feasible:
            update["notes"].append(
                f"返程车次均早于行程末日最后安排（{latest_end}）结束，"
                "可能赶不上；建议调整末日安排或改选更晚车次")
        elif infeasible:
            update["notes"].append(
                f"部分返程车次早于末日最后安排（{latest_end}）结束，"
                "已标注衔接可行性")


def _execute_weather(
    task: TravelAdditionalTask,
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict:
    params = task.params
    brief = state.get("brief") or {}
    city = (params.city or params.destination
            or (brief.get("destination") if isinstance(brief, dict) else "")
            or "").strip()
    if not city:
        return _task_record(
            task, status="needs_clarification", data_status="missing_inputs",
            message="查天气需要知道城市。",
            missing_fields=["city"],
        )

    from backend.travel.services import weather_service

    def _fetch() -> dict:
        forecast, reason = weather_service.fetch_forecast(city)
        if forecast is None:
            raise LiveSearchError(reason or "天气预报暂时不可用")
        return forecast

    def _summary(data: dict) -> dict:
        days = data.get("days") or []
        preview = [
            {
                "date": item.get("date") or "",
                "weather": ((item.get("day") or {}).get("weather") or ""),
            }
            for item in days[:5] if isinstance(item, dict)
        ]
        return {
            "category": "weather",
            "result_count": len(days),
            "data_status": "available" if days else "empty",
            "preview": preview,
        }

    tool = "travel.weather.query"
    data, outcome, record = _run_checked_task(
        task, tool=tool, agent="research", provider=PROVIDER_QWEATHER,
        call=_fetch, summarize=_summary,
    )
    if data is None:
        _append_policy_records(update, state, tool, PROVIDER_QWEATHER, outcome)
    return record


def _execute_place(
    task: TravelAdditionalTask,
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict:
    params = task.params
    brief = state.get("brief") or {}
    city = (params.city or params.destination
            or (brief.get("destination") if isinstance(brief, dict) else "")
            or "").strip()
    if not city:
        return _task_record(
            task, status="needs_clarification", data_status="missing_inputs",
            message="查询景点或酒店需要知道城市。",
            missing_fields=["city"],
        )

    from backend.travel.services import live_search_service

    is_hotel = task.type == "query_hotel"
    tool = "map_merchant_search_tool"

    def _call() -> dict:
        if is_hotel:
            return live_search_service.search_hotels(city)
        return live_search_service.search_attractions(
            keyword="景点", city=city, page_size=8)

    def _summary(data: dict) -> dict:
        return live_search_service.merchant_preview(
            data, "hotel" if is_hotel else "poi")

    data, outcome, record = _run_checked_task(
        task,
        tool=tool,
        agent="research",
        provider=PROVIDER_AMAP,
        call=_call,
        summarize=_summary,
    )
    if data is None:
        _append_policy_records(update, state, tool, PROVIDER_AMAP, outcome)
    return record


def _execute_food(
    task: TravelAdditionalTask,
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict:
    """Dining Scout（收编重构 U2）：高德商户检索 + L3 偏好过滤。

    偏好来自 travel.memory.search（用户长期记忆，如「不辣/清淡」），
    确定性关键词过滤（商户名/类目命中忌口词即剔除）——不做语义推断，
    过滤与否全部留痕。选入建议走既有 selections/meals 端点（本节点不写）。
    """
    params = task.params
    brief = state.get("brief") or {}
    city = (params.city or params.destination
            or (brief.get("destination") if isinstance(brief, dict) else "")
            or "").strip()
    if not city:
        return _task_record(
            task, status="needs_clarification", data_status="missing_inputs",
            message="查美食需要知道城市。",
            missing_fields=["city"],
        )

    from backend.travel.services import live_search_service

    tool = "map_merchant_search_tool"

    def _summary(data: dict) -> dict:
        return live_search_service.merchant_preview(data, "food")

    data, outcome, record = _run_checked_task(
        task, tool=tool, agent="research", provider=PROVIDER_AMAP,
        call=lambda: live_search_service.search_food(city),
        summarize=_summary,
    )
    if data is None:
        _append_policy_records(update, state, tool, PROVIDER_AMAP, outcome)
        return record

    prefs, pref_note = _dietary_preferences_from_memory()
    if prefs:
        kept, dropped = _filter_by_dietary(data.get("merchants") or [], prefs)
        data["merchants"] = kept
        record["result_count"] = len(kept)
        record["preference_filter"] = {
            "preferences": prefs, "dropped": dropped}
        record["message"] = (
            f"已按你的饮食偏好（{'、'.join(prefs)}）过滤 {dropped} 家商户")
        update["notes"].append(
            f"美食推荐已按记忆中的饮食偏好（{'、'.join(prefs)}）过滤")
    elif pref_note:
        update["notes"].append(pref_note)
    return record


# 忌口/偏好词 → 商户名/类目的命中排除词（确定性词表；不覆盖语义变体，
# 未覆盖的偏好只留痕不过滤——宁多推荐不误删）
_DIETARY_EXCLUDES: dict[str, tuple[str, ...]] = {
    "不辣": ("辣", "川菜", "湘菜", "火锅", "麻辣"),
    "不吃辣": ("辣", "川菜", "湘菜", "火锅", "麻辣"),
    "忌辣": ("辣", "川菜", "湘菜", "火锅", "麻辣"),
    "清淡": ("辣", "火锅", "麻辣", "烧烤", "油炸"),
    "素食": ("烤肉", "烧烤", "火锅", "牛", "羊", "猪"),
}
_PREFERENCE_QUERY = "饮食偏好 忌口 口味 不辣 清淡 素食"


def _dietary_preferences_from_memory() -> tuple[list[str], str]:
    """从 L3 记忆提取显式饮食偏好（软失败：无记忆/检索失败 = 无偏好）。"""
    try:
        from backend.tools.memory import memory_search_tool

        raw = memory_search_tool.invoke({"query": _PREFERENCE_QUERY, "top_k": 5})
        text = str(raw)
        if not text.strip():
            return [], ""
        import json as _json

        try:
            payload = _json.loads(text)
            hits = payload.get("results") or payload.get("data") or []
        except ValueError:
            hits = []
        blob = text if not hits else _json.dumps(hits, ensure_ascii=False)
        found: list[str] = []
        for pref in _DIETARY_EXCLUDES:
            if pref in blob and pref not in found:
                found.append(pref)
        return found, ""
    except Exception as e:  # noqa: BLE001 — 偏好检索失败不影响美食推荐主链
        logger.debug("[TravelAuxiliaryTasks] 饮食偏好检索失败: %s", e)
        return [], "饮食偏好记忆不可用，本次未做偏好过滤"


def _filter_by_dietary(
    merchants: list, prefs: list[str],
) -> tuple[list, int]:
    """确定性偏好过滤：商户名/类目命中排除词即剔除。"""
    excludes: list[str] = []
    for pref in prefs:
        excludes.extend(_DIETARY_EXCLUDES.get(pref, ()))
    kept, dropped = [], 0
    for merchant in merchants:
        if not isinstance(merchant, dict):
            continue
        text = (str(merchant.get("name") or "") + " "
                + str(merchant.get("category") or ""))
        if any(token in text for token in excludes):
            dropped += 1
            continue
        kept.append(merchant)
    return kept, dropped


def auxiliary_tasks_node(state: dict) -> dict:
    """最多执行四个经契约校验、每种类型至多一次的可选实时查询。"""
    raw_tasks = (
        (state.get("turn_decision") or {}).get("additional_tasks") or [])
    results = list(state.get("task_results") or [])
    existing_ids = {
        str(item.get("task_id") or "")
        for item in results if isinstance(item, dict)
    }
    existing_types = {
        str(item.get("type") or "")
        for item in results if isinstance(item, dict)
    }
    update: dict[str, Any] = {
        "task_results": results,
        "transit_query": state.get("transit_query") or {},
        "notes": list(state.get("notes") or []),
        "degraded_tools": list(state.get("degraded_tools") or []),
        "blocked_tools": list(state.get("blocked_tools") or []),
        "tool_failures": list(state.get("tool_failures") or []),
    }
    if not raw_tasks:
        return update

    for index, raw in enumerate(raw_tasks):
        if index >= _MAX_TASKS_PER_TURN:
            logger.warning("[TravelAuxiliaryTasks] 超出单轮任务上限，跳过 task")
            raw_overflow = raw if isinstance(raw, dict) else {}
            results.append({
                "task_id": str(raw_overflow.get("task_id") or f"overflow-{index}"),
                "type": str(raw_overflow.get("type") or ""),
                "status": "skipped_limit",
                "data_status": "not_executed",
                "result_count": 0,
                "data": {},
                "message": "单轮附加查询数量已达上限。",
            })
            continue
        try:
            task = TravelAdditionalTask.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — checkpoint 状态也必须重新校验
            logger.info("[TravelAuxiliaryTasks] 拒绝非法任务: %s", exc)
            raw = raw if isinstance(raw, dict) else {}
            results.append({
                "task_id": str(raw.get("task_id") or f"invalid-{index}"),
                "type": str(raw.get("type") or ""),
                "status": "rejected",
                "data_status": "invalid_request",
                "result_count": 0,
                "data": {},
            })
            continue
        if task.task_id in existing_ids or task.type in existing_types:
            continue
        existing_ids.add(task.task_id)
        existing_types.add(task.type)

        if task.type == "query_train":
            record = _execute_train(task, state, update)
        elif task.type == "query_weather":
            record = _execute_weather(task, state, update)
        elif task.type in {"query_poi", "query_hotel"}:
            record = _execute_place(task, state, update)
        elif task.type == "query_food":
            record = _execute_food(task, state, update)
        else:  # Pydantic Literal 已拦截；保留 fail-closed 分支
            record = _task_record(
                task, status="rejected", data_status="unsupported",
                message="不支持的旅游查询类型。",
            )
        results.append(record)

    update["task_results"] = results
    return update


__all__ = ["auxiliary_tasks_node"]
