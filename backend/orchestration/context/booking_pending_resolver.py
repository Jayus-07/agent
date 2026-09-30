"""booking_pending_resolver.py — 交易挂起续填 Resolver（Phase 5 / D2 两跳断修复）

架构位置（与 TravelPendingResolver 并列，同在 ContinuationResolver 之前）：

    Guard → Context Assembler → TravelPendingResolver → **BookingPendingResolver**
          → ContinuationResolver → Coarse Domain Router

解决的问题（实测 D2）：预订/比价两子图**无 checkpointer**，澄清期参数不在
PG。用户说「帮我预订大阪的酒店」→ 子图反问「哪天入住？」→ 用户答「10月3日」
→ **掉域**。原因是这句纯槽位值回答：
  - travel_prefilter 不命中（0 信号词 0 城市）；
  - ContinuationResolver 不命中（延续信号表只覆盖「改成3天/太赶了」类指令）；
  - booking/commerce prefilter 也不命中（没有「订/酒店」等意图词）。
没人接住 → 落回主 Router / CS 兜底。

修法：交易子图澄清时把结构化挂起 `booking_intent` 写入 ConversationContext
（G3，适配器侧），并把活跃域登记为 travel_booking/travel_commerce（G1）。
本模块是**路由层判定点**：活跃域为交易两域且挂起存在时，判定本轮 query
能否补上 ≥1 个缺失槽位——能则短路回原子图（子图 resolver 走续填档）。

判定契约（对齐 travel_pending_resolver，纯规则零 LLM 零 IO）：
  1. 开关 BOOKING_PENDING_RESUME_ENABLED 关 → 不拦（紧急回滚用）；
  2. 活跃域必须是 travel_booking / travel_commerce 且有匹配的结构化挂起
     （route_mode/kind/missing_slots 齐备，缺一不拦）；
  3. 对应域总开关关 → 放行正常路由（域关时挂起不该存在，退化即不介入）；
  4. 客服强信号在场 → 放行（「订单里的行程单」属客服，CS 优先铁律一致）；
  5. 命中判定 = 本轮补上 ≥1 个缺失槽位（抽取与子图**同源**：
     commerce/extract 的 merge_slot_values，绝不另写一套解析而与主链分叉）；
  6. 异常一律放行（fail-open to normal routing）。

命中后只回「回哪个子图」（route_mode）；缺失/已收槽位的**结构化挂起读写
由子图适配器完成**（唯一抽取点仍是子图 + 其服务层），本层不碰业务事实。
"""
from __future__ import annotations

from backend.shared.logger import logger

__all__ = ["resolve_booking_pending"]

# 交易两域：预订单图与比价子图（route_mode 与 active_domain 同值口径）
_TRANSACTION_DOMAINS: frozenset[str] = frozenset(
    {"travel_booking", "travel_commerce"})


def _domain_enabled(route_mode: str) -> bool:
    """对应交易域总开关（域关 → 不介入，交正常路由）。异常按关闭处理。"""
    try:
        if route_mode == "travel_booking":
            from backend.config.travel_booking import is_booking_enabled
            return is_booking_enabled()
        from backend.config.travel_commerce import is_commerce_enabled
        return is_commerce_enabled()
    except Exception:  # noqa: BLE001 — 配置件故障按关闭处理
        return False


def resolve_booking_pending(
    query: str, routing_context: dict | None
) -> dict | None:
    """交易（预订/比价）挂起续填判定统一入口。

    Args:
        query: 本轮用户输入（normalize 后）
        routing_context: Context Assembler 产出（active_domain /
            brief_summary.booking_intent）

    Returns:
        命中 → ``{"route_decision": None, "route_mode": <原子图 mode>}``
        （与 booking_prefilter 同构；挂起读写由子图适配器完成）；
        未命中/不适用 → None。
    """
    query = (query or "").strip()
    if not query or len(query) > 40:
        return None

    try:
        from backend.config.travel import BOOKING_PENDING_RESUME_ENABLED
        if not BOOKING_PENDING_RESUME_ENABLED:
            return None
    except Exception:  # noqa: BLE001
        return None

    ctx = routing_context or {}
    if (ctx.get("active_domain") or "").strip() not in _TRANSACTION_DOMAINS:
        return None  # 无活跃交易任务，永不拦

    summary = ctx.get("brief_summary") or {}
    intent = summary.get("booking_intent") or {}
    route_mode = (intent.get("route_mode") or "").strip()
    kind = (intent.get("kind") or "").strip()
    missing = [s for s in (intent.get("missing_slots") or []) if s]
    collected = dict(intent.get("collected") or {})
    if (route_mode not in _TRANSACTION_DOMAINS or not kind or not missing):
        return None  # 挂起不完整（无结构化缺失项），交正常路由

    if not _domain_enabled(route_mode):
        return None  # 域总开关关 → 不介入

    # 客服等其他域强信号在场 → 放行（不与 CS 优先铁律竞争；复用旅游
    # pending 同一阈值口径，避免两处判定分叉）
    from backend.orchestration.context.travel_pending_resolver import (
        _has_cs_strong_signal,
    )
    if _has_cs_strong_signal(query):
        return None

    # 命中判定：本轮补上 ≥1 个缺失槽位即回子图续填。
    # 抽取与子图同源（merge_slot_values 内仍是 extract_hotel_params /
    # extract_flight_params），本层只比较缺失数量，不外传槽位值。
    from backend.travel.commerce.extract import merge_slot_values
    _, missing_after = merge_slot_values(kind, query, collected)
    if len(missing_after) >= len(missing):
        return None  # 一个槽位都没补上 → 交回正常路由（原样短路）

    logger.info(
        "[BookingPendingResolver] 命中: mode=%s kind=%s missing %s→%s",
        route_mode, kind, missing, missing_after,
    )
    return {"route_decision": None, "route_mode": route_mode}
