"""routing/continuation.py — 跨轮续跑与旅游/交易 pending（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留）：
  - _try_continuation：ContinuationResolver 命中 → 回活跃域
  - try_travel_pending：Travel Pending Resolver（STOP F2，先于 ContinuationResolver）
  - try_booking_pending：交易挂起续填 Resolver（Phase 5 / D2），与 travel 并列
"""
from __future__ import annotations

from backend.shared.logger import logger


def _try_continuation(state: dict, query: str, routing_context: dict) -> dict | None:
    """ContinuationResolver 命中 → 直接回活跃域（复用既有域图入口）。

    仅处理有状态域图（travel/customer_service/selection_funnel）；工具域
    （data/knowledge/...）无跨轮图状态可回，交正常路由。任何异常都返回
    None 走原有链路，绝不阻断。
    """
    try:
        from backend.orchestration.context.continuation_resolver import (
            resolve_continuation,
        )

        cont = resolve_continuation(query, routing_context)
    except Exception as e:
        logger.warning(f"[RouterNode] 延续判定失败，走正常路由: {e}")
        return None

    if not cont.get("is_continuation"):
        if cont.get("matched"):
            logger.info(
                f"[RouterNode] 延续信号未回域: reason={cont.get('reason')} "
                f"matched={cont.get('matched')}"
            )
        return None

    domain = cont["domain"]
    logger.info(
        f"[RouterNode] 延续命中: domain={domain} matched={cont.get('matched')}"
    )
    try:
        from backend.observability.tracer import trace_collector

        t = trace_collector.current()
        if t is not None:
            t.tags["continuation"] = f"{domain}:{cont.get('matched', '')}"
    except Exception:
        pass

    if domain == "travel":
        # 与 try_travel_prefilter 同构的域图入口；目的地/天数变更由域图
        # slot_filler 在 checkpoint 状态上处理（跨轮契约不变）
        return {
            "route_decision": None,
            "route_mode": "travel",
            "travel_context": {
                "conversation_id": state.get("session_id", ""),
                "travel_route": {"source": "continuation"},
            },
        }
    if domain == "customer_service":
        try:
            from backend.orchestration.graph.cs_prefilter import try_cs_prefilter

            update = try_cs_prefilter(query, state, forced=True)
            if update is not None:
                return update
        except Exception as e:
            logger.warning(f"[RouterNode] 延续回客服域失败，走正常路由: {e}")
        return None
    if domain == "selection_funnel":
        try:
            from backend.orchestration.graph.selection_funnel_prefilter import (
                try_selection_funnel_prefilter,
            )

            update = try_selection_funnel_prefilter(query, state)
            if update is not None:
                return update
        except Exception as e:
            logger.warning(f"[RouterNode] 延续回选品域失败，走正常路由: {e}")
    return None


def try_travel_pending(query: str, routing_context: dict) -> dict | None:
    """Travel Pending Resolver（STOP F2，原 router_node 主函数内联段）。

    活跃 travel 任务有结构化 pending 时，纯槽位值回答（「8万日元」
    「住难波」）短路回旅游域——这类话既无旅游信号也无延续信号，
    不拦就丢上下文（追问没人接住）。命中条件与 NEW_RUN 判定见
    travel_pending_resolver 模块 docstring；客服强信号在其内部放行。
    异常软失败返回 None（走正常路由）。
    """
    try:
        from backend.orchestration.context.travel_pending_resolver import (
            resolve_travel_pending,
        )

        return resolve_travel_pending(query, routing_context)
    except Exception as e:
        logger.warning(f"[RouterNode] travel pending 判定失败，走正常路由: {e}")
        return None


def try_booking_pending(query: str, routing_context: dict) -> dict | None:
    """交易挂起续填 Resolver（Phase 5 / D2，与 try_travel_pending 并列）。

    活跃域为 travel_booking / travel_commerce 且存在结构化 booking_intent
    挂起时，判定纯槽位值回答（「10月3日」「大阪」）能否补上缺失参数——
    能则短路回原子图（子图 resolver 走续填档），否则交回正常路由。
    命中条件与异常语义见 booking_pending_resolver 模块 docstring；
    客服强信号在其内部放行。异常软失败返回 None（走正常路由）。
    """
    try:
        from backend.orchestration.context.booking_pending_resolver import (
            resolve_booking_pending,
        )

        return resolve_booking_pending(query, routing_context)
    except Exception as e:
        logger.warning(f"[RouterNode] 交易挂起判定失败，走正常路由: {e}")
        return None
