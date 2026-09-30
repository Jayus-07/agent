"""routing/prefilter_chain.py — 决策组装 + 域 prefilter 链（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留）：
  - _ROUTE_MODE_DOMAIN / _mark_route_from_update：prefilter 命中回写会话路由上下文
  - _with_router_decisions：新适配器决策对象组装（STOP B Router 收口）
  - _try_general_chat：Guard GREETING 直答分流
  - cs_rule_hits_of：CS 廉价规则预判（入口预过滤顺序第一步）
  - _try_cs_prefilter：CS 预过滤薄包装
  - run_domain_prefilters：旅游 → 选品 → 预订 → 商务四连 prefilter

依赖方向（方案冻结）：prefilter_chain 不 import 本包其他模块；
被 router_node / hierarchical 消费。
"""
from __future__ import annotations

from backend.shared.logger import logger

# route_mode → ConversationContext.active_domain（预过滤命中回写用）。
# 客服 clarify（route_mode=clarify，出自 cs_prefilter）也归属客服域。
_ROUTE_MODE_DOMAIN = {
    "travel": "travel",
    "customer_service": "customer_service",
    "selection_funnel": "selection_funnel",
}


def _mark_route_from_update(state: dict, update: dict) -> dict:
    """域图 prefilter 命中 → 回写会话路由上下文（Context Assembler 回写侧）。

    下一轮 Context Assembler 据此组装 active_domain，ContinuationResolver
    据此把「改成3天」「太赶了」这类跨轮短指令拉回活跃域。软失败不影响路由。
    """
    try:
        mode = (update or {}).get("route_mode") or ""
        domain = _ROUTE_MODE_DOMAIN.get(mode)
        pending = None
        clarify = (update or {}).get("_clarify")
        if isinstance(clarify, dict) and clarify.get("question"):
            pending = clarify["question"]
        if domain or (mode == "clarify" and (update or {}).get("cs_context") is not None):
            from backend.orchestration.context.routing_context import mark_domain_turn

            mark_domain_turn(
                state.get("tenant_id") or "", state.get("user_id") or "",
                state.get("session_id") or "",
                domain=domain or "customer_service",
                intent=mode, action=domain or mode,
                pending_question=pending,
            )
    except Exception:
        logger.debug("[RouterNode] 路由上下文回写失败（软降级）", exc_info=True)
    return update


def _with_router_decisions(
    state: dict,
    update: dict,
    query: str,
    *,
    existing_override=None,
    hierarchical_meta: dict | None = None,
) -> dict:
    """把新适配器结果增量写回 state，旧路由字段仍保持权威。

    该函数只做决策对象组装，不执行 Tool/Skill/Workflow；prefilter 的调用顺序
    仍由 ``router_node`` 原有分支控制。任何适配器异常都软失败，不阻断旧路径。
    """

    result = {**state, **(update or {})}
    try:
        from backend.orchestration.router.capability_router import CapabilityRouter
        from backend.orchestration.router.domain_router import DomainRouter
        from backend.orchestration.router.execution_mode import ExecutionModeResolver
        from backend.orchestration.domain_registry import domain_graph_registry

        domain_router = DomainRouter()
        if hierarchical_meta is not None:
            domain_decision = DomainRouter.from_hierarchical_meta(
                hierarchical_meta,
            )
        elif hasattr(existing_override, "model_dump") or (
            isinstance(existing_override, dict)
            and (
                "execution_mode" in existing_override
                or "candidates" in existing_override
            )
        ):
            # legacy 已经完成 rule/vector/LLM 拍板，不重复做一次 embedding。
            domain_decision = {
                "domain": "unknown",
                "subflow": None,
                "confidence": 0.0,
                "source": "legacy",
                "reasoning": "legacy RouteDecision 已完成域外拍板",
            }
        else:
            domain_decision = domain_router.route(
                query, state, prefilter_update=update,
            )

        if hierarchical_meta is not None:
            capability_decision = CapabilityRouter.from_routing_meta(
                domain_decision["domain"], hierarchical_meta,
            )
        elif (
            hasattr(existing_override, "model_dump")
            or (
                isinstance(existing_override, dict)
                and (
                    "execution_mode" in existing_override
                    or "candidates" in existing_override
                )
            )
        ):
            capability_decision = CapabilityRouter.from_route_decision(
                domain_decision["domain"], existing_override,
            )
        else:
            capability_decision = {
                "domain": domain_decision["domain"],
                "capability": None,
                "candidates": [],
                "confidence": 0.0,
                "source": "prefilter",
                "reasoning": "域图/兼容短路不选择主图 capability",
            }

        resolver_override = existing_override or update
        if isinstance(existing_override, dict):
            nested_decision = existing_override.get("route_decision")
            if isinstance(nested_decision, dict):
                resolver_override = {
                    **nested_decision,
                    "route_mode": existing_override.get("route_mode") or "",
                }
        registered_domains = domain_graph_registry.get_all()
        graph_modes = {name: name for name in registered_domains}
        execution_decision = ExecutionModeResolver(
            domain_graph_modes=graph_modes or None,
        ).resolve(
            domain_decision,
            capability_decision,
            resolver_override,
        )
        current_legacy_used = bool((update or {}).get("legacy_used", False))
        if hierarchical_meta is None and (
            hasattr(existing_override, "model_dump")
            or (
                isinstance(existing_override, dict)
                and (
                    "execution_mode" in existing_override
                    or "candidates" in existing_override
                )
            )
        ):
            current_legacy_used = True
        if isinstance(existing_override, dict):
            nested_decision = existing_override.get("route_decision")
            if isinstance(nested_decision, dict) and nested_decision.get(
                "execution_mode"
            ):
                current_legacy_used = True
        result.update({
            "domain_decision": domain_decision,
            "capability_decision": capability_decision,
            "execution_decision": execution_decision.to_dict(),
            "router_fallback_reason": (update or {}).get(
                "router_fallback_reason", "",
            ),
            "legacy_used": current_legacy_used,
        })
        from backend.orchestration.router.router_trace import record_router_decision

        record_router_decision(
            domain_decision,
            capability_decision,
            execution_decision.to_dict(),
        )
    except Exception as exc:
        logger.warning("[RouterNode] Router 决策适配器失败，保持旧字段: %s", exc)
        result.update({
            "router_fallback_reason": f"decision_adapter:{exc}",
            "legacy_used": True,
        })
    return result


def _try_general_chat(state: dict) -> dict | None:
    """问候/能力咨询（Guard GREETING 判定）→ general_chat 直答分流。

    仅全局入口生效；客服窗口锁域语义不变（寒暄仍进 CS 管线）。
    """
    try:
        category = ((state.get("guard_result") or {}).get("category") or "")
        if category == "greeting":
            logger.info("[RouterNode] Guard GREETING → general_chat 直答")
            return {
                "route_decision": None,
                "route_mode": "general_chat",
                "query_understanding": {"intent": "greeting", "complexity": "chat"},
            }
    except Exception:
        logger.debug("[RouterNode] general_chat 判定失败，走正常路由", exc_info=True)
    return None


def cs_rule_hits_of(query: str) -> int:
    """CS 廉价规则预判（入口预过滤顺序第一步，抽自 router_node 主函数）。

    失败保守视作命中（返回 1），维持 CS 优先。
    """
    try:
        from backend.customer_service.router.domain_detector import cs_rule_hit_count
        return cs_rule_hit_count(query)
    except Exception as e:
        logger.debug(f"[RouterNode] CS 规则预判失败，按旧顺序处理: {e}")
        return 1  # 保守：视作命中，维持 CS 优先


def _try_cs_prefilter(query: str, state: dict, forced: bool = False) -> dict | None:
    """CS 预过滤薄包装（原 router_node 主函数内嵌 def，抽为模块级）。"""
    # 逻辑在 cs_prefilter.py（只判断"是不是客服"，不判断"走哪个 expert"）
    try:
        from backend.orchestration.graph.cs_prefilter import try_cs_prefilter
        return try_cs_prefilter(query, state, forced=forced)
    except Exception as e:
        logger.warning(f"[RouterNode] CS 预过滤失败，回退到主 Router: {e}")
        return None


def run_domain_prefilters(query: str, state: dict) -> dict | None:
    """旅游 → 选品 → 预订 → 商务四连 prefilter（抽自 router_node 主函数）。

    命中即返回完整 state 更新（含决策组装与路由上下文回写）；
    全部未命中返回 None（主函数继续 CS 兜底）。异常各段独立软失败回退。
    """
    try:
        from backend.orchestration.graph.travel_prefilter import try_travel_prefilter
        travel_update = try_travel_prefilter(query, state)
        if travel_update is not None:
            update = _mark_route_from_update(state, travel_update)
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    except Exception as e:
        logger.warning(f"[RouterNode] 旅游预过滤失败，回退到主 Router: {e}")

    # ── 选品漏斗预过滤：纯正则（~1ms），与旅游同层（2026-09-17 接线）──
    # 「给宠物零食做一次智能选品」这类请求短路进选品漏斗域图；语义与
    # selection_decision workflow（上不上架决策）通过 _DECISION_EXCLUDE 互斥。
    try:
        from backend.orchestration.graph.selection_funnel_prefilter import (
            try_selection_funnel_prefilter,
        )
        funnel_update = try_selection_funnel_prefilter(query, state)
        if funnel_update is not None:
            update = _mark_route_from_update(state, funnel_update)
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    except Exception as e:
        logger.warning(f"[RouterNode] 选品预过滤失败，回退到主 Router: {e}")

    # ── 预订域预过滤（STOP L9）：选品与商务之间加法插入 ──
    # 「订/预订」是交易意图，先于商务承接（L0 §20 冻结触碰：既有四域
    # 两两顺序不变，仅新增一域；CS 售后/行程信号已在词表让路）。
    try:
        from backend.orchestration.graph.booking_prefilter import (
            try_booking_prefilter,
        )
        booking_update = try_booking_prefilter(query, state)
        if booking_update is not None:
            update = _mark_route_from_update(state, booking_update)
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    except Exception as e:
        logger.warning(f"[RouterNode] 预订预过滤失败，回退到主 Router: {e}")

    # ── 商务预过滤（STOP K6）：纯正则，旅游/选品之后 ──
    # 「找酒店/查机票」类纯库存查询短路进 commerce 域图；行程信号词
    # （酒店推荐/住宿推荐/行程/攻略）已由旅游 prefilter 先行承接或在
    # extract 内让路——优先级：客服 > 旅游 > 选品 > 商务（STOPK0 §1）。
    try:
        from backend.orchestration.graph.commerce_prefilter import (
            try_commerce_prefilter,
        )
        commerce_update = try_commerce_prefilter(query, state)
        if commerce_update is not None:
            update = _mark_route_from_update(state, commerce_update)
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    except Exception as e:
        logger.warning(f"[RouterNode] 商务预过滤失败，回退到主 Router: {e}")

    return None
