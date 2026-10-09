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
    if data is None:
        _append_policy_records(
            update, state, "travel_train_search_tool", PROVIDER_12306, outcome)
    return record


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
        else:  # Pydantic Literal 已拦截；保留 fail-closed 分支
            record = _task_record(
                task, status="rejected", data_status="unsupported",
                message="不支持的旅游查询类型。",
            )
        results.append(record)

    update["task_results"] = results
    return update


__all__ = ["auxiliary_tasks_node"]
