"""旅游域 Trace 语义投影。

业务状态是事实源，Trace 只从最终状态和本轮结果派生，不允许再由入口
根据用户文案猜测「是否规划」「是否改单」。这样 REST、SSE 和主图入口
使用同一套 planning_mode / 版本 / 局部改单口径。
"""
from __future__ import annotations

from typing import Any

from backend.travel.graph_state import brief_fingerprint
from backend.travel.models.brief import TravelBrief


_NON_PLANNING_INTENTS = {
    "social",
    "meta",
    "out_of_scope",
    "query_static",
    "query_dynamic",
    "query_transit",
    "discover",
}
_QUERY_INTENTS = {"query_static", "query_dynamic", "query_transit", "discover"}


def _as_dict(value: Any) -> dict:
    """把可选的状态快照安全地归一为 dict。"""
    return value if isinstance(value, dict) else {}


def _version(value: Any) -> int | str:
    """版本保留数字语义；无版本时返回空串，避免伪造 v0。"""
    if value in (None, ""):
        return ""
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _fingerprint(raw: Any) -> str:
    """对已有 brief 计算业务指纹；不完整快照按模型默认值补齐。"""
    brief = _as_dict(raw)
    if not brief:
        return ""
    try:
        return brief_fingerprint(TravelBrief.model_validate(brief))
    except Exception:  # noqa: BLE001 — 观测旁路不能阻塞业务结果
        return ""


def _modified_days(state: dict, partial_result: dict) -> list[int]:
    raw = (state.get("changed_days") or partial_result.get("changed_days") or [])
    days: list[int] = []
    for value in raw:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if day not in days:
            days.append(day)
    return days


def build_trace_semantics(state: dict | None, result: dict | None = None) -> dict:
    """构建 STOP7 要求的完整 Trace 语义字段。

    ``active_plan_version`` 对修改轮指向修改前的已确认/基底版本，
    ``draft_plan_version`` 指向本轮产生的候选版本；首次规划没有基底时，
    两者都不伪造。查询和社交回答不携带规划版本。
    """
    state = state or {}
    result = result or {}
    intent = str(state.get("intent") or result.get("intent") or "")
    partial_result = _as_dict(state.get("partial_replan_result"))
    partial_request = _as_dict(state.get("partial_replan"))
    partial = bool(partial_result.get("partial_replan") or partial_request)

    if intent == "social":
        mode = "social"
    elif intent in _QUERY_INTENTS:
        mode = "query"
    elif intent in _NON_PLANNING_INTENTS:
        mode = "social"
    elif partial or intent == "modify":
        mode = "modify"
    else:
        mode = "plan"

    itinerary = _as_dict(state.get("itinerary") or result.get("itinerary"))
    baseline = _as_dict(state.get("plan_stability_baseline"))
    base_itinerary = _as_dict(baseline.get("itinerary"))
    base_brief = base_itinerary.get("brief") or baseline.get("brief")
    candidate_brief = state.get("brief") or result.get("brief")
    if not candidate_brief:
        candidate_brief = itinerary.get("brief")

    base_fp = _fingerprint(base_brief)
    candidate_fp = _fingerprint(candidate_brief)
    dirty = list(state.get("brief_changed_fields") or [])
    # 局部改单只修改已有行程，不等于需求 brief 发生语义变化；
    # 预算/目的地等槽位变化则由 dirty 或两份指纹差异明确表示。
    semantic_change = bool(dirty) or bool(
        base_fp and candidate_fp and base_fp != candidate_fp
    )

    current_version = _version(itinerary.get("plan_version"))
    base_version = _version(
        base_itinerary.get("plan_version")
        or baseline.get("plan_version")
        or state.get("plan_parent_version")
    )
    if mode in {"social", "query"}:
        active_version: int | str = ""
        draft_version: int | str = ""
    elif mode == "modify":
        active_version = base_version or current_version
        draft_version = current_version
    else:
        active_version = base_version
        draft_version = current_version

    # API 版本账本在落库后提供更准确的 active/draft 投影；单元测试和
    # 直接调用域图时没有这段旁路数据，继续使用上面的状态推导。
    projection = _as_dict(state.get("_trace_plan_versions"))
    if mode not in {"social", "query"}:
        base_version = _version(
            projection.get("base_plan_version") or base_version)
        active_version = _version(
            projection.get("active_plan_version") or active_version)
        draft_version = _version(
            projection.get("draft_plan_version") or draft_version)

    operation = str(
        partial_result.get("operation")
        or partial_request.get("operation")
        or ""
    )
    modified_days = _modified_days(state, partial_result)
    expert_history = state.get("expert_history") or []
    tool_count = 0 if mode in {"social", "query"} else len(expert_history)

    # Failure Policy 结局投影（2026-10-07 容错契约）：降级了哪些 Tool、
    # 是否发生硬依赖阻断——「Tool failed ≠ run failed」在 trace 层可判。
    degraded_tools = [
        str((item or {}).get("tool") or "")
        for item in (state.get("degraded_tools") or [])
        if isinstance(item, dict)
    ]
    blocked_tools = [
        str((item or {}).get("tool") or "")
        for item in (state.get("blocked_tools") or [])
        if isinstance(item, dict)
    ]

    # LLM 理解层观测（2026-10-08 STOP 1/4）：槽位解析来源与两次 LLM 调用
    # 的结局投影。全部标量、无敏感原文（用户消息不进 tags）。
    slot_llm = _as_dict(state.get("slot_llm_meta"))
    clarify_meta = _as_dict(state.get("clarification_meta"))

    return {
        "conversation_id": str(state.get("conversation_id") or ""),
        "intent": intent,
        "interaction_mode": mode,
        "base_plan_version": base_version,
        "active_plan_version": active_version,
        "draft_plan_version": draft_version,
        "base_brief_fingerprint": base_fp,
        "candidate_brief_fingerprint": candidate_fp,
        "semantic_change": semantic_change,
        "modification_operation": operation,
        "modified_days": modified_days,
        "planning_mode": mode,
        "full_replan": mode == "plan" and bool(itinerary),
        "partial_replan": partial,
        "tool_count": tool_count,
        "degraded_tools": [t for t in degraded_tools if t],
        "blocked_tools": [t for t in blocked_tools if t],
        "workflow_continued": not bool(blocked_tools),
        "slot_parse_source": str(state.get("slot_parse_source") or "rule"),
        "slot_llm_used": str(bool(slot_llm.get("used"))).lower(),
        "slot_llm_model": str(slot_llm.get("model") or ""),
        "slot_llm_prompt_version": str(slot_llm.get("prompt_version") or ""),
        "slot_llm_latency_ms": slot_llm.get("latency_ms") or 0,
        "slot_llm_candidate_count": slot_llm.get("candidate_count") or 0,
        "slot_llm_accepted_count": slot_llm.get("accepted_count") or 0,
        "slot_llm_rejected_count": slot_llm.get("rejected_count") or 0,
        "slot_llm_fallback_reason": str(slot_llm.get("fallback_reason") or ""),
        "clarification_source": str(state.get("clarification_source") or ""),
        "clarification_slot": str(clarify_meta.get("slot") or ""),
        "clarification_prompt_version": str(
            clarify_meta.get("prompt_version") or ""),
        "clarification_latency_ms": clarify_meta.get("latency_ms") or 0,
        "clarification_fallback_reason": str(
            clarify_meta.get("fallback_reason") or ""),
    }


__all__ = ["build_trace_semantics"]
