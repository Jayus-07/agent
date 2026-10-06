"""域 RuntimeResult 适配器。

该模块只做结构化归一与兼容字段并存，不生成文本、不调用模型，也不改写域图
已经产出的 final_answer 或业务 payload。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.types import RuntimeResult, normalize_runtime_result


_RUNTIME_RESULT_FIELDS = {
    "answer",
    "final_answer",
    "answer_type",
    "sources",
    "artifacts",
    "tool_calls",
    "ui_payload",
    "clarification",
    "handoff",
    "error",
    "metadata",
    "status",
    "runtime_result",
}

_STATUS_MAP = {
    "success": "success",
    "ok": "success",
    "answered": "success",
    "partial": "partial",
    "no_data": "partial",
    "clarification": "clarification",
    "needs_clarification": "clarification",
    "handoff": "handoff",
    "needs_handoff": "handoff",
    "error": "error",
    "failed": "error",
}


def _runtime_status(update: Mapping[str, Any]) -> str:
    status = str(update.get("status") or "success")
    if update.get("clarification") is not None or update.get("_clarify") is not None:
        return "clarification"
    if update.get("handoff") is not None or update.get("_handoff") is not None:
        return "handoff"
    return _STATUS_MAP.get(status, "partial")


def _clarification_value(
    update: Mapping[str, Any],
    clarification: dict[str, Any] | str | None,
) -> dict[str, Any] | None:
    value = clarification
    if value is None:
        value = update.get("clarification") or update.get("_clarify")
    if value is None:
        travel_context = update.get("travel_context") or {}
        if isinstance(travel_context, Mapping):
            value = travel_context.get("pending_decision")
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return {"question": str(value)}


def _handoff_value(
    update: Mapping[str, Any],
    handoff: dict[str, Any] | str | None,
) -> dict[str, Any] | None:
    value = handoff
    if value is None:
        value = update.get("handoff") or update.get("_handoff")
    if value is None:
        cs_context = update.get("cs_context") or {}
        if isinstance(cs_context, Mapping):
            state = cs_context.get("handoff_state")
            if state and state != "ai_active":
                value = {"state": state}
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return {"state": str(value)}


def build_runtime_result(
    state_update: Mapping[str, Any],
    *,
    runtime_id: str,
    ui_payload: Mapping[str, Any] | None = None,
    sources: list[Any] | None = None,
    clarification: dict[str, Any] | str | None = None,
    handoff: dict[str, Any] | str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    status: str | None = None,
) -> RuntimeResult:
    """把一个域适配器的旧 State 更新归一为 RuntimeResult。"""

    update = dict(state_update)
    payload = (
        dict(ui_payload)
        if ui_payload is not None
        else {
            key: value
            for key, value in update.items()
            if key not in _RUNTIME_RESULT_FIELDS
        }
    )
    update["status"] = status or _runtime_status(update)
    update["ui_payload"] = payload or None

    normalized_clarification = _clarification_value(update, clarification)
    if normalized_clarification is not None:
        update["clarification"] = normalized_clarification
    normalized_handoff = _handoff_value(update, handoff)
    if normalized_handoff is not None:
        update["handoff"] = normalized_handoff
    if sources is not None:
        update["sources"] = list(sources)
    if tool_calls is not None:
        update["tool_calls"] = list(tool_calls)
    if metadata is not None:
        update["metadata"] = dict(metadata)

    return normalize_runtime_result(update, runtime_id=runtime_id)


def attach_runtime_result(
    state_update: Mapping[str, Any],
    *,
    runtime_id: str,
    ui_payload: Mapping[str, Any] | None = None,
    sources: list[Any] | None = None,
    clarification: dict[str, Any] | str | None = None,
    handoff: dict[str, Any] | str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """在保留旧域 State 的同时附加可序列化 runtime_result。"""

    result = build_runtime_result(
        state_update,
        runtime_id=runtime_id,
        ui_payload=ui_payload,
        sources=sources,
        clarification=clarification,
        handoff=handoff,
        tool_calls=tool_calls,
        metadata=metadata,
        status=status,
    )
    return {
        **dict(state_update),
        "runtime_result": result.model_dump(mode="json"),
    }
