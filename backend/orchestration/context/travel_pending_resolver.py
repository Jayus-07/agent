"""travel_pending_resolver.py — Travel Pending Resume Resolver（STOP F2）

架构位置（任务书 §10，插在 ContinuationResolver 之前）：

    Guard → Context Assembler → **TravelPendingResolver** → ContinuationResolver
          → Coarse Domain Router

解决的问题：旅游域追问后的**纯槽位值回答**（「8万日元」「住难波」「3天」）
不含任何旅游/延续信号词——travel_prefilter 不命中（0 信号词 0 城市）、
ContinuationResolver 也不命中（信号表只覆盖「改成3天/太赶了」类指令）。
结果是上一轮「预算多少？」的追问没人接住，travel 上下文丢失。

判定契约（任务书 §23：明确 pending → zero/one cheap extraction）：
  1. 活跃域必须是 travel 且存在结构化 travel_pending（缺一不拦）；
  2. NEW_RUN 显式信号优先（「重新规划」+ 新需求）→ 换 run 短路回域图；
  3. 规则 slot 解析（复用 slot_filler 纯函数，零 LLM）命中值型补槽槽位
     ≥1 个 → CONTINUE/PATCH/REPLAN 短路回域图（user_message 原样透传，
     域图 slot_filler 是唯一抽取点，本模块只做「是否命中」的判定）；
  4. 客服等其他域强信号在场 → 放行正常路由（「订单里的行程单」属客服，
     与 CS 优先铁律一致）；判定异常一律放行（fail-open to normal routing）。

纯规则零 LLM 零 IO；输入是 Context Assembler 产出的 dict，可单测穷举。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger

__all__ = ["resolve_travel_pending"]

# 值型补槽槽位白名单（任务书 §三 contract）：集合型槽位（preferences/
# must_go/avoid）不参与 pending 解析——长句误命中率高，且「改单类」表达
# 已由 ContinuationResolver 的延续信号接住（范围控制，登记 F0 文档）。
_VALUE_SLOTS: tuple[str, ...] = (
    "destination", "days", "start_date", "party_size", "budget_cny",
    "lodging",
)


# 客服强信号放行（复用 CS 规则判定，阈值与 continuation_resolver 同口径 ≥2）
def _has_cs_strong_signal(query: str) -> bool:
    try:
        from backend.customer_service.router.domain_detector import (
            cs_rule_hit_count,
        )

        return cs_rule_hit_count(query) >= 2
    except Exception:  # noqa: BLE001 — 信号件故障按无信号处理
        return False


def _extract_slot(query: str, slot: str) -> bool:
    """单槽位探测：命中返回 True（值不外传——域图 slot_filler 是唯一抽取点）。

    party_size 只认 explicit 来源（「带爸妈」的 guess 不算用户在回答追问）。
    """
    try:
        from backend.travel.slot_filler import (
            extract_budget,
            extract_days,
            extract_destination,
            extract_lodging,
            extract_party_size,
            extract_start_date,
            party_size_source,
        )

        if slot == "destination":
            return bool(extract_destination(query))
        if slot == "days":
            return extract_days(query) is not None
        if slot == "start_date":
            return extract_start_date(query) is not None
        if slot == "budget_cny":
            return extract_budget(query) is not None
        if slot == "lodging":
            return bool(extract_lodging(query))
        if slot == "party_size":
            return (extract_party_size(query) is not None
                    and party_size_source(query) == "explicit")
        return False
    except Exception:  # noqa: BLE001 — 抽取件故障按未命中处理
        logger.debug("[TravelPendingResolver] slot 探测异常: slot=%s", slot,
                     exc_info=True)
        return False


def _resolve_slots(query: str, requested_slots: list[str]) -> list[str]:
    """对 pending 请求的槽位（∪ 值型补槽全集）逐槽探测，返回命中列表。"""
    slots: list[str] = []
    for slot in dict.fromkeys(list(requested_slots or []) + list(_VALUE_SLOTS)):
        if slot in _VALUE_SLOTS and _extract_slot(query, slot):
            slots.append(slot)
    return slots


def resolve_travel_pending(query: str, routing_context: dict | None) -> dict | None:
    """旅游 pending 补槽判定统一入口。

    Args:
        query: 本轮用户输入（normalize 后）
        routing_context: Context Assembler 产出（active_domain /
            brief_summary.travel_pending / brief_summary.travel_run_id）

    Returns:
        命中 → route_mode="travel" 的 state 更新（与 travel_prefilter 同构，
        travel_route 携带 resume 观测标记）；未命中/不适用 → None。
    """
    query = (query or "").strip()
    if not query or len(query) > 40:
        return None

    try:
        from backend.config.travel import TRAVEL_PENDING_RESUME_ENABLED
        if not TRAVEL_PENDING_RESUME_ENABLED:
            return None
    except Exception:  # noqa: BLE001
        return None

    ctx = routing_context or {}
    if (ctx.get("active_domain") or "") != "travel":
        return None  # 无活跃 travel 任务，永不拦（任务书 §3 规则 1）
    summary = ctx.get("brief_summary") or {}
    pending = summary.get("travel_pending") or {}
    requested = pending.get("requested_slots") or []

    # 取消整个规划（STOP G3）：活跃 run 期间显式取消 → 短路回域图，
    # 由 travel_graph_node 执行 CANCEL_TRAVEL_RUN（本层只判定不改状态）。
    # 保守：仅 run 存在且未取消过才拦（completed 后反悔仍可取消——
    # STOP H-D1 实机修复：「这次旅行不规划了」在出单后同样成立）；
    # 「不去鼓浪屿了」类局部排除句由 is_cancel_run_query 自行排除。
    summary_run = summary.get("travel_run_id") or ""
    summary_stage = summary.get("travel_stage") or ""
    if summary_run and summary_stage != "cancelled":
        from backend.travel.slot_filler import is_cancel_run_query

        if is_cancel_run_query(query):
            logger.info(
                "[TravelPendingResolver] 命中: mode=cancel run=%s", summary_run)
            return {
                "route_decision": None,
                "route_mode": "travel",
                "travel_context": {
                    "conversation_id": ctx.get("conversation_id") or "",
                    "travel_route": {
                        "source": "pending_resume",
                        "resume_mode": "cancel",
                        "new_run": False,
                        "requested_slots": list(requested or []),
                        "filled_slots": [],
                    },
                },
            }

    if not requested:
        return None  # 无结构化 pending（既有任务完成/无追问），不拦

    # 客服等其他域强信号在场 → 放行（不与 CS 优先铁律竞争）
    if _has_cs_strong_signal(query):
        return None

    from backend.travel.slot_filler import is_new_run_query

    new_run = is_new_run_query(query)
    filled = _resolve_slots(query, requested)
    if not new_run and not filled:
        return None  # 短答案但一个槽位都没对上 → 交回正常路由

    resume_mode = "new_run" if new_run else "continue"
    logger.info(
        "[TravelPendingResolver] 命中: mode=%s run=%s requested=%s filled=%s",
        resume_mode, summary.get("travel_run_id") or "-", requested, filled,
    )
    return {
        "route_decision": None,
        "route_mode": "travel",
        "travel_context": {
            "conversation_id": ctx.get("conversation_id") or "",
            "travel_route": {
                "source": "pending_resume",
                "resume_mode": resume_mode,
                "new_run": new_run,
                "requested_slots": list(requested),
                "filled_slots": filled,
            },
        },
    }
