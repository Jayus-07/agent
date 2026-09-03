"""router_node.py — Router LangGraph 节点（2026-08-11）

3 层 fallback Router 接入 LangGraph：
  - Router 节点在 graph 入口
  - RouteDecision 存到 state.route_decision
  - conditional_edges 按 execution_mode 分流

V1 实现:
  - plan mode → 走现有 planner → critique → supervisor 路径
  - workflow mode → stub（V1 未完整实现，后续接 workflow_name）
  - direct mode → V1 降级为 plan（后续可加 single-skill node）
"""
from __future__ import annotations

import time

from backend.orchestration.router import get_router
from backend.shared.logger import logger


def router_node(state: dict) -> dict:
    """Router 节点：执行 3 层 fallback 路由，存 decision 到 state。

    Returns:
        包含 route_decision 的 state 更新
    """
    query = state.get("question") or state.get("query") or ""
    if not query:
        return {
            **state,
            "route_decision": None,
            "route_mode": "plan",
        }

    # ── CS 预过滤：CS_ENABLED 时先检测客服域，命中则短路不进主 Router ──
    try:
        from backend.config.customer_service import CS_ENABLED
        if CS_ENABLED:
            from backend.customer_service.router.domain_detector import get_domain_detector
            detection = get_domain_detector().detect(query)
            if detection.is_cs:
                from backend.customer_service.router.cs_router import get_cs_router
                cs_result = get_cs_router().route(query, detection)
                route_path = cs_result.route_path.value

                from backend.observability.metrics import record_cs_intent
                record_cs_intent(cs_result.intent)

                if route_path == "knowledge_query":
                    cs_target = "cs_knowledge"
                elif route_path == "business_query":
                    cs_target = "cs_business_query"
                elif route_path == "business_action":
                    cs_target = "cs_business_action"
                elif route_path == "complaint_flow":
                    cs_target = "cs_complaint"
                elif route_path == "human_handoff":
                    cs_target = "cs_handoff"
                else:
                    cs_target = "cs_pending"

                logger.info(
                    f"[RouterNode] CS 域命中: domain={cs_result.domain.value} "
                    f"intent={cs_result.intent} conf={cs_result.confidence:.2f} "
                    f"→ {cs_target}"
                )

                # ── Phase 5: CS Input Guard ──
                from backend.customer_service.security.input_guard import get_cs_input_guard
                user_id = state.get("user_id", "anonymous")
                session_id = state.get("session_id", "default")
                guard_result = get_cs_input_guard().check(query)
                if guard_result.action.value == "block":
                    logger.info(
                        f"[RouterNode] CS InputGuard BLOCK: "
                        f"category={guard_result.category.value} reason={guard_result.reason}"
                    )
                    return {
                        **state,
                        "route_decision": None,
                        "route_mode": "customer_service",
                        "cs_context": {
                            "cs_route": cs_result.model_dump(),
                            "cs_target": cs_target,
                            "authenticated_user_id": user_id,
                            "session_id": session_id,
                            "conversation_id": session_id,
                        },
                        "final_answer": guard_result.message,
                    }

                # ── Phase 5: 转接拦截 — 活跃转接时拦截业务请求 ──
                _handoff_terminal_targets = {"cs_handoff", "cs_complaint", "cs_handoff_intercept"}
                if cs_target not in _handoff_terminal_targets:
                    from backend.customer_service.handoff_store import get_handoff_store
                    if get_handoff_store().has_active_handoff(user_id):
                        logger.info(
                            f"[RouterNode] 转接拦截: user={user_id} "
                            f"原目标={cs_target} → cs_handoff_intercept"
                        )
                        cs_target = "cs_handoff_intercept"

                # ── Trace: stamp conversation_id + cs_route ──
                try:
                    from backend.observability.tracer import trace_collector
                    trace = trace_collector.current()
                    if trace is not None:
                        trace.tags["conversation_id"] = session_id
                        trace.metadata["cs_route"] = {
                            "intent": cs_result.intent,
                            "domain": cs_result.domain.value,
                            "confidence": cs_result.confidence,
                            "target": cs_target,
                        }
                except Exception:
                    pass

                return {
                    **state,
                    "route_decision": None,
                    "route_mode": "customer_service",
                    "cs_context": {
                        "cs_route": cs_result.model_dump(),
                        "cs_target": cs_target,
                        "authenticated_user_id": user_id,
                        "session_id": session_id,
                        "conversation_id": session_id,
                    },
                }
    except Exception as e:
        logger.warning(f"[RouterNode] CS 预过滤失败，回退到主 Router: {e}")

    try:
        # P0-4: get_router() 懒加载（router 索引/向量资源首次初始化）曾贡献
        # 数秒无埋点黑洞；单独成 span 使其在瀑布图中可见（埋点软失败）。
        try:
            from backend.observability.tracer import trace_collector
            init_span = trace_collector.start_span(
                "router_init", name="Router 初始化", type="tool_call",
                input={"query_len": len(query)})
            t_init = time.time()
            try:
                router = get_router()
            finally:
                trace_collector.end_span(
                    init_span,
                    metrics={"init_ms": int((time.time() - t_init) * 1000)})
        except Exception:
            logger.debug("[RouterNode] router_init span 记录失败", exc_info=True)
            router = get_router()
        # 同步调用（router 主流程是同步的）
        decision = router.route(query)
        logger.info(
            f"[RouterNode] mode={decision.execution_mode.value} "
            f"conf={decision.confidence:.2f} "
            f"cands={[(c.name, c.score) for c in decision.candidates]}"
        )
    except Exception as e:
        logger.warning(f"[RouterNode] 路由失败，回退到 plan: {e}")
        return {
            **state,
            "route_decision": None,
            "route_mode": "plan",
        }

    # V2: workflow / direct 不再降级到 plan
    mode = decision.execution_mode
    return {
        **state,
        "route_decision": decision.model_dump(),
        "route_mode": mode.value,
    }


def route_selector(state: dict) -> str:
    """Router 节点后的条件路由：选择下一步节点（V2: 3 路分流 + CS）。"""
    mode = state.get("route_mode", "plan")
    if mode == "direct":
        return "skill_executor"
    if mode == "workflow":
        return "workflow_executor"
    if mode == "customer_service":
        cs_context = state.get("cs_context", {})
        return cs_context.get("cs_target", "cs_knowledge")
    return "planner"
