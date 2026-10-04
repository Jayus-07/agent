"""routing_context.py — Context Assembler（路由入口重构 2026-09-22）

目标架构（用户规格）：
    Guard → Context Assembler → ContinuationResolver → Coarse Domain Router → ...

本模块是 Guard 之后的第二站：
  - assemble_routing_context(): 从 ConversationContext 组装 Router 可读的
    会话上下文（active_domain / last_intent / last_action /
    brief_summary / pending_question），注入 state["routing_context"]
    并随 route_context 传给粗分类器；
  - mark_domain_turn(): 路由决策/域图产出后回写任务状态（谁写的谁回写，
    与 sync_travel_brief_to_context 的「TravelBrief 单向同步」互补——
    本模块只记路由级事实，不碰业务槽位）；
  - set_pending_question(): 追问/澄清产生时记录待答问题。

职责边界：
  - 只做读写与结构组装，零判定逻辑（判定在 continuation_resolver.py）；
  - 全程软失败：上下文不可用绝不阻断主聊天链；
  - 数据源 = 既有进程内 ConversationContext，不新增持久化。
"""
from __future__ import annotations

from backend.shared.logger import logger

__all__ = [
    "assemble_routing_context",
    "mark_domain_turn",
    "set_pending_question",
]


def assemble_routing_context(
    tenant_id: str, user_id: str, session_id: str
) -> dict:
    """组装 Router 消费的会话上下文（Guard 之后、Router 之前调用一次）。

    STOP G：数据源切 ConversationContextRepository（Redis shared），
    读失败/未命中返回空上下文（active_domain="" → ContinuationResolver
    不介入），与原进程内 peek 语义一致。

    Returns:
        {
            "active_domain": str,   # 上一任务域（空 = 无活跃任务）
            "last_intent": str,      # 上一轮路由意图
            "last_action": str,      # 上一轮动作（tool/域图/clarify）
            "brief_summary": dict,   # 跨域摘要槽位（travel brief 为主）
            "pending_question": str, # 上一轮留下的待答问题
        }
    """
    empty = {
        "active_domain": "", "last_intent": "", "last_action": "",
        "brief_summary": {}, "pending_question": "", "sql_query_context": None,
    }
    if not session_id:
        return empty
    try:
        from backend.orchestration.context.context_repository import (
            get_conversation_context_repository,
        )

        ctx = get_conversation_context_repository().peek(
            tenant_id or "", user_id or "", session_id)
        if ctx is None:
            return empty
        snap = ctx.snapshot()
        return {
            # conversation_id 供 TravelPendingResolver 等消费方直接取用
            # （与 get/peek 的会话键同一口径）
            "conversation_id": session_id,
            "active_domain": snap.get("active_domain", ""),
            "last_intent": snap.get("last_intent", ""),
            "last_action": snap.get("last_action", ""),
            "brief_summary": {
                k: v for k, v in snap.items()
                if k not in ("active_domain", "last_intent", "last_action",
                             "pending_question", "sql_query_context")
            },
            "pending_question": snap.get("pending_question", ""),
            "sql_query_context": snap.get("sql_query_context"),
        }
    except Exception as exc:  # noqa: BLE001 — 上下文不可用不阻断主链
        logger.debug("[RoutingContext] 组装失败，返回空上下文: %s", exc)
        return empty


def mark_domain_turn(
    tenant_id: str, user_id: str, session_id: str, *,
    domain: str, intent: str = "", action: str = "",
    pending_question: str | None = None,
) -> None:
    """回写一轮路由结果（软失败，fire-and-forget）。

    调用点：router_node 的 prefilter 命中 / 粗分类拍板 / clarify 路径。
    STOP G：走 repository 原子 mutation（MARK_TURN），跨 worker 一致。
    """
    if not session_id or not domain:
        return
    try:
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )

        get_conversation_context_repository().mutate(
            tenant_id or "", user_id or "", session_id,
            ContextMutation(MutationType.MARK_TURN, {
                "domain": domain, "intent": intent, "action": action,
                "pending_question": pending_question,
            }))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RoutingContext] 回写失败（软降级）: %s", exc)


def set_pending_question(
    tenant_id: str, user_id: str, session_id: str, question: str
) -> None:
    """记录待用户回答的问题（追问卡 / 澄清 / 域图待决项）。"""
    if not session_id or not question:
        return
    try:
        from backend.orchestration.context.context_repository import (
            ContextMutation,
            MutationType,
            get_conversation_context_repository,
        )

        get_conversation_context_repository().mutate(
            tenant_id or "", user_id or "", session_id,
            ContextMutation(MutationType.MARK_TURN,
                            {"domain": "", "pending_question": question[:120]}))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RoutingContext] 待答问题记录失败（软降级）: %s", exc)
