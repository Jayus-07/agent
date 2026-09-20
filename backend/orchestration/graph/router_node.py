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


def _enrich_with_understanding(cs_update: dict, query: str) -> dict:
    """P1 步骤 3 接线（2026-09-19）：CSUnderstanding 结果并入 cs_route。

    - 实体（订单号等）入 cs_route.metadata —— action/query expert 直接消费，
      取代各自散落的正则抽取入口；
    - normalized_text / missing_slots / decision_layer 随 cs_route 进状态；
    - decision_layer 写 Trace tags（P1 完成标准：路由 Trace 可查决策层）；
    - 纯规则零 LLM；任何异常软降级（不阻塞路由）。
    """
    try:
        from backend.customer_service.understanding import build_understanding
        # cs_prefilter 的产出：route_mode + cs_context（TypedDict，cs_route
        # 在 cs_context 内，兼容未来顶层直挂的形态）
        ctx = cs_update.get("cs_context")
        cs_route = ctx.get("cs_route") if isinstance(ctx, dict) else None
        if not isinstance(cs_route, dict):
            cs_route = cs_update.get("cs_route")
        if not isinstance(cs_route, dict):
            return cs_update
        u = build_understanding(query, cs_route)
        metadata = cs_route.setdefault("metadata", {})
        order_ids = u.entity_values("order_id")
        if order_ids:
            metadata.setdefault("order_id", order_ids[0])
        metadata["entities"] = [e.model_dump() for e in u.entities]
        metadata["missing_slots"] = u.missing_slots
        metadata["normalized_text"] = u.normalized_text
        metadata["decision_layer"] = u.decision_layer.value
        try:
            from backend.observability.tracer import trace_collector
            t = trace_collector.current()
            if t is not None:
                t.tags["decision_layer"] = u.decision_layer.value
                if u.entities:
                    t.tags["cs_entities"] = ",".join(
                        f"{e.type.value}:{e.match()}" for e in u.entities[:6])
        except Exception:
            pass
        return cs_update
    except Exception as e:
        logger.warning(f"[RouterNode] CSUnderstanding 软降级: {e}")
        return cs_update


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

    # ── 预过滤顺序（2026-09-15 定序；2026-09-18 起已无性能收益）──────
    # 该顺序最初为省掉 CS 向量通道的云端 embedding 往返而设计（2026-09-15
    # 实测 1.0~3.4s / 次）。3f88b4f（2026-09-18）删除向量通道后，CS 检测
    # 已退化为纯正则（实测冷路径 ~21µs、命中缓存 ~1µs），顺序不再带来可观
    # 耗时收益；保留它是为**判定语义**而非性能：
    #   1) CS 廉价规则预判（纯正则）：命中 → 立即做完整 CS 检测（保客服优先）
    #   2) 旅游纯正则预过滤：命中 → 短路进旅游域
    #   3) 都没命中 → 完整 CS 检测兜底
    # 对"订单里的行程单"这类同时含 CS 规则的 query：规则命中 → 仍走 CS
    # 优先，与旧行为一致。
    # 注：1) 与 3) 现在同源（都走 _rule_channel 正则），属可收拢的冗余；
    # 收拢会改判定语义，需另立变更单，勿在注释/文档修订里顺手改。
    try:
        from backend.customer_service.router.domain_detector import cs_rule_hit_count
        cs_rule_hits = cs_rule_hit_count(query)
    except Exception as e:
        logger.debug(f"[RouterNode] CS 规则预判失败，按旧顺序处理: {e}")
        cs_rule_hits = 1  # 保守：视作命中，维持 CS 优先

    # ── 入口域锁（2026-09-18）：客服窗口（CSDrawer）每条消息带
    # domain_hint=customer_service。用户已显式进入客服窗口，若每条消息重新
    # 判域，非客服问法会被甩到主图 plan 支线白烧 LLM（实测"下周去大阪怎么玩"
    # 全局入口 cs规则=0 → route_mode=plan）；且 CS 规则阈值 CS_RULE_MIN_HITS=2
    # 漏掉"东西坏了咋办"这类高频问法（实测命中仅 1）。故锁域强制走 CS 预过滤：
    # 跳过域检测门/灰度判定/旅游与选品 prefilter；仅保留 CS_ENABLED 总闸
    # （CS 关闭时降级回主路由，与全局开关语义一致）。
    # 域锁非绝对：redirect_main 会把命中旅游/选品强信号的问法转出主路由，但
    # 该正则只认种子城市（福州/厦门/杭州）——"去大阪怎么玩"仍留守客服管线。
    domain_hint = (state.get("domain_hint") or "").strip().lower()
    cs_forced = domain_hint in ("customer_service", "cs")

    def _try_cs_prefilter(forced: bool = False) -> dict | None:
        # 逻辑在 cs_prefilter.py（只判断"是不是客服"，不判断"走哪个 expert"）
        try:
            from backend.orchestration.graph.cs_prefilter import try_cs_prefilter
            return try_cs_prefilter(query, state, forced=forced)
        except Exception as e:
            logger.warning(f"[RouterNode] CS 预过滤失败，回退到主 Router: {e}")
            return None

    # ── redirect_main 阶段一（2026-09-18，确定性正则）────────────────
    # 域锁下"明显非客服"的问法转出主路由：无任何客服规则信号，且命中
    # 旅游/选品强信号 → 不进 CS，放行后续 prefilter 自然路由，抽屉内也能
    # 拿到旅游/选品的正常回答（设计稿第 4 节 next_action=redirect_main 的
    # 确定性子集）。混合信号（如"订单里的行程单怎么退款"含客服规则）仍守
    # CS 优先——与主路由既有判定一致，避免旅游关键词抢走客服流量。
    # 阶段二（2026-09-18，LLM 语义仲裁）：正则未命中时交给 non_cs 检测器
    # 判 non_cs_confidence，≥阈值才转出。默认 OFF（CS_REDIRECT_MAIN_LLM_ENABLED）。
    cs_redirect = None
    if cs_forced and not cs_rule_hits:
        try:
            from backend.orchestration.graph.travel_prefilter import is_travel_request
            from backend.orchestration.graph.selection_funnel_prefilter import (
                is_selection_funnel_request,
            )
            if is_travel_request(query):
                cs_redirect = "travel_regex_hit"
            elif is_selection_funnel_request(query):
                cs_redirect = "selection_funnel_regex_hit"
        except Exception as e:
            logger.debug(f"[RouterNode] redirect_main 正则判定失败，维持锁域: {e}")
        # 阶段二：正则未命中的域锁 query 走 LLM 语义仲裁（软失败留守 CS）。
        if cs_redirect is None:
            try:
                from backend.customer_service.analyzer.non_cs_detector import (
                    detect_non_cs_cached,
                    should_redirect,
                )
                det = detect_non_cs_cached(query)
                if det is not None and should_redirect(det):
                    cs_redirect = f"llm_non_cs:{det.target_domain or 'unknown'}"
            except Exception as e:
                logger.debug(f"[RouterNode] redirect_main LLM 仲裁失败，维持锁域: {e}")
        if cs_redirect:
            logger.info(f"[RouterNode] CS 域锁转出(redirect_main): {cs_redirect} → 主路由")
            try:
                from backend.observability.tracer import trace_collector
                t = trace_collector.current()
                if t is not None:
                    t.tags["cs_redirect_main"] = cs_redirect
            except Exception:
                pass

    if cs_forced and not cs_redirect:
        cs_update = _try_cs_prefilter(forced=True)
        if cs_update is not None:
            return {**state, **_enrich_with_understanding(cs_update, query)}
    elif not cs_forced and cs_rule_hits:
        cs_update = _try_cs_prefilter()
        if cs_update is not None:
            return {**state, **_enrich_with_understanding(cs_update, query)}

    # ── 旅游预过滤：纯正则 ─────────────────────────────────────────
    # 域锁且未转出时跳过；转出（redirect_main）或全局入口正常执行。
    if not cs_forced or cs_redirect:
        try:
            from backend.orchestration.graph.travel_prefilter import try_travel_prefilter
            travel_update = try_travel_prefilter(query, state)
            if travel_update is not None:
                return {**state, **travel_update}
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
                return {**state, **funnel_update}
        except Exception as e:
            logger.warning(f"[RouterNode] 选品预过滤失败，回退到主 Router: {e}")

    # ── CS 兜底（全局入口）：无 CS 规则命中时再走一次完整 CS 预过滤 ──
    # 检测器已无向量通道（3f88b4f 删除），与上面的规则预判同源；保留是为
    # 走通 detect_cached + 灰度判定路径，行为与旧版一致。
    if not cs_rule_hits and not cs_forced:
        cs_update = _try_cs_prefilter()
        if cs_update is not None:
            return {**state, **_enrich_with_understanding(cs_update, query)}

    # ── L1 入口弱命中追问（2026-09-19 拒答转追问）────────────────
    # 放在全部域预过滤与 CS 兜底之后：客服优先级不被追问抢夺。
    # 只拦"差一个槽位就能进域"的输入（如 1 个旅游信号词没说城市），
    # 短路不进主 Router，避免这类输入跑完链路后只换来一句拒答。
    # _clarify 只活在节点原始输出里（stream_node_events 从这里发
    # clarification 事件），不依赖 graph state 传递。
    # 防循环：会话 10 分钟内已被追问过一次则放行原链路（后续拒答
    # 也不会再追问，同一守卫）。
    try:
        from backend.orchestration.graph.clarify_content import (
            build_entry_clarify,
            clarify_allowed,
            mark_clarified,
        )
        clarify = build_entry_clarify(query, domain_hint)
        if clarify is not None and not clarify_allowed(
                state.get("session_id", "")):
            clarify = None
        elif clarify is not None:
            mark_clarified(state.get("session_id", ""))
    except Exception as e:
        logger.warning(f"[RouterNode] 入口追问判定失败，走原链路: {e}")
        clarify = None
    if clarify is not None:
        logger.info(f"[RouterNode] L1 弱命中追问: source={clarify.get('source')}")
        return {
            **state,
            "route_decision": None,
            "route_mode": "clarify",
            "_clarify": clarify,
        }

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
        # 同步调用（router 主流程是同步的）。
        # context 进入路由缓存键（预留位）：同文 query 在不同 user/department
        # 下不会互串缓存。当前路由决策本身不依赖上下文，故不改变行为。
        route_context = {
            "department": state.get("department") or "",
            "user_id": state.get("user_id") or "",
        }
        decision = router.route(query, context=route_context)
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
    if mode == "clarify":
        # L1 弱命中追问：直接到 reporter 出短文案（builder edge_map
        # "clarify" → "reporter"），不执行任何 skill
        return "clarify"
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(mode)
    if domain:
        return domain.node_name
    return "planner"
