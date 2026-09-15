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

    # ── 预过滤顺序（2026-09-15 性能优化，判定语义保持不变）──────────
    # 背景：CS 检测器双通道，向量通道每次请求一次云端 embedding 往返
    # （实测 1.0~3.4s）；旅游预过滤是纯正则（~1ms）。原先无条件先跑完整
    # CS 检测 → 旅游/普通请求白烧一次 embedding。
    # 新顺序：
    #   1) CS 廉价规则预判（~1ms）：命中 → 立即做完整 CS 检测（保客服优先）
    #   2) 旅游纯正则预过滤（~1ms）：命中 → 短路进旅游域
    #   3) 都没命中 → 完整 CS 检测（含向量通道，保留语义兜底路径）
    # 对"订单里的行程单"这类同时含 CS 规则的 query：规则命中 → 仍走 CS
    # 优先，与旧行为一致。
    try:
        from backend.customer_service.router.domain_detector import cs_rule_hit_count
        cs_rule_hits = cs_rule_hit_count(query)
    except Exception as e:
        logger.debug(f"[RouterNode] CS 规则预判失败，按旧顺序处理: {e}")
        cs_rule_hits = 1  # 保守：视作命中，维持 CS 优先

    def _try_cs_prefilter() -> dict | None:
        # 逻辑在 cs_prefilter.py（只判断"是不是客服"，不判断"走哪个 expert"）
        try:
            from backend.orchestration.graph.cs_prefilter import try_cs_prefilter
            return try_cs_prefilter(query, state)
        except Exception as e:
            logger.warning(f"[RouterNode] CS 预过滤失败，回退到主 Router: {e}")
            return None

    if cs_rule_hits:
        cs_update = _try_cs_prefilter()
        if cs_update is not None:
            return {**state, **cs_update}

    # ── 旅游预过滤：纯正则，先于 CS 向量检测执行（省一次 embedding）──
    try:
        from backend.orchestration.graph.travel_prefilter import try_travel_prefilter
        travel_update = try_travel_prefilter(query, state)
        if travel_update is not None:
            return {**state, **travel_update}
    except Exception as e:
        logger.warning(f"[RouterNode] 旅游预过滤失败，回退到主 Router: {e}")

    # ── CS 语义兜底：无 CS 规则命中时，向量通道仍可能判定为客服域 ──
    if not cs_rule_hits:
        cs_update = _try_cs_prefilter()
        if cs_update is not None:
            return {**state, **cs_update}

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
    """Router 节点后的条件路由：选择下一步节点。

    内置路径: direct → skill_executor, workflow → workflow_executor, 默认 → planner
    域图路径: 通过 domain_graph_registry 动态查找
    """
    mode = state.get("route_mode", "plan")
    if mode == "direct":
        return "skill_executor"
    if mode == "workflow":
        return "workflow_executor"
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(mode)
    if domain:
        return domain.node_name
    return "planner"
