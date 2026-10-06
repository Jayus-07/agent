"""travel/models/graph_result.py — 旅游域图输出契约

与 CSGraphResult 同一职责：解耦「域图内部状态」与「主图字段」。
主图只认这个结构，域图内部状态怎么改都不会漏到主图。

status 语义：
  success             — 行程单已生成
  needs_clarification — 必填槽位缺失，本次返回的是追问
  no_data             — 目的地没有可用数据
  failed              — 流程异常（含排程失败）
"""
from __future__ import annotations

from typing import Any, TypedDict

STATUS_SUCCESS = "success"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_NO_DATA = "no_data"
STATUS_FAILED = "failed"
STATUS_ANSWERED = "answered"


class TravelGraphResult(TypedDict, total=False):
    final_answer: str
    status: str
    brief: dict
    itinerary: dict | None
    validation: dict | None
    clarification: str
    clarification_options: list[dict]
    intent: str
    travel_context: dict
    rationale: dict


def build_travel_graph_result(final_state: dict[str, Any]) -> TravelGraphResult:
    """从域图最终状态构建输出契约。"""
    brief = final_state.get("brief") or {}
    missing = final_state.get("brief_missing") or []
    itinerary = final_state.get("itinerary")
    candidates = final_state.get("candidates") or []
    failed_experts = [
        item for item in (final_state.get("expert_history") or [])
        if item.get("status") == "failed"
    ]

    partial_result = final_state.get("partial_replan_result") or {}
    answering = final_state.get("intent") in {
        "social", "meta", "out_of_scope", "query_static", "query_dynamic",
        "discover",
    } or (final_state.get("intent") == "modify" and not partial_result)

    if answering:
        status = STATUS_ANSWERED
        itinerary = None
    elif partial_result.get("validation_failed"):
        status = STATUS_FAILED
    elif failed_experts:
        status = STATUS_FAILED
    elif missing:
        status = STATUS_NEEDS_CLARIFICATION
    elif not candidates:
        status = STATUS_NO_DATA
    elif final_state.get("final_answer") and itinerary:
        status = STATUS_SUCCESS
    else:
        status = STATUS_FAILED

    clarifications = final_state.get("clarifications") or []

    return TravelGraphResult(
        final_answer=final_state.get("final_answer", ""),
        status=status,
        brief=brief,
        itinerary=itinerary,
        validation=None if answering else final_state.get("validation"),
        clarification=clarifications[0] if clarifications and not answering else "",
        clarification_options=[] if answering else final_state.get("clarification_options", []),
        intent=final_state.get("intent", ""),
        travel_context=final_state.get("travel_context") or {},
        rationale=final_state.get("rationale") or {},
    )
