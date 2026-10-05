"""router_node.py — Router LangGraph 节点（主编排）

统一 RoutingEngine 接入 LangGraph：
  - Router 节点在 graph 入口
  - RouteDecision 存到 state.route_decision
  - conditional_edges 按 execution_mode 分流

P1-2 拆分（2026-09-30 主架构改造）：决策组装/域 prefilter 链/锁域/
续跑/CS 理解增强/分层路由分派已纯移动至 ``routing/`` 子包（行为零变更），
本文件只保留 router_node 主函数（编排顺序）+ route_selector +
全部旧符号 re-export（既有引用方 import 路径不变）。
"""
from __future__ import annotations

import time

from backend.orchestration.graph.routing import (
    _enrich_with_understanding,
    _handle_hierarchical_meta,
    _hierarchical_state_fields,
    _mark_route_from_update,
    _try_continuation,
    _try_cs_prefilter,
    _try_general_chat,
    _with_router_decisions,
    cs_rule_hits_of,
    detect_cs_redirect,
    is_cs_forced,
    run_domain_prefilters,
    try_booking_pending,
    try_travel_pending,
)
from backend.orchestration.router import get_routing_engine
from backend.shared.logger import logger

# ── 旧符号 re-export（P1-2 拆分兼容层，勿删：tests/潜在引用方在用）──
# 顶部主编排 import 已带入 _enrich_with_understanding/_with_router_decisions/
# _try_continuation/_try_general_chat/_mark_route_from_update 等；此处补齐
# 其余历史符号，保证 from router_node import <任何旧名> 不破。
from backend.orchestration.graph.routing import _ROUTE_MODE_DOMAIN  # noqa: F401


def router_node(state: dict) -> dict:
    """Router 节点：执行统一路由引擎，存 decision 到 state。

    Returns:
        包含 route_decision 的 state 更新
    """
    query = state.get("question") or state.get("query") or ""
    if not query:
        return _with_router_decisions(
            state,
            {"route_decision": None, "route_mode": "plan"},
            query,
            existing_override={"route_mode": "plan"},
        )

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
    cs_rule_hits = cs_rule_hits_of(query)

    # ── 入口域锁（2026-09-18）：客服窗口（CSDrawer）每条消息带
    # domain_hint=customer_service，锁域强制走 CS 预过滤：跳过域检测门/
    # 灰度判定/旅游与选品 prefilter；仅保留 CS_ENABLED 总闸（CS 关闭时降级
    # 回主路由，与全局开关语义一致）。判定逻辑见 routing/lock_domain.py。
    # 域锁非绝对：redirect_main 会把命中旅游/选品强信号的问法转出主路由，
    # 但该正则只认种子城市（福州/厦门/杭州）——"去大阪怎么玩"仍留守客服管线。
    # domain_hint 规范化值保留在主函数：L1 追问判定（build_entry_clarify）
    # 也消费它，与拆分前同源。
    domain_hint = (state.get("domain_hint") or "").strip().lower()
    cs_forced = is_cs_forced(state)

    # ── 路由入口重构（2026-09-22）：Guard → Context Assembler →
    # ContinuationResolver → Coarse Domain Router ──────────────────
    # routing_context 由 runner 在图外组装（assemble_routing_context），
    # 缺省空 dict = 无活跃任务，延续判定自动失效。
    routing_context = state.get("routing_context") or {}
    if not cs_forced:
        # Travel Pending Resolver（STOP F2，先于 ContinuationResolver），
        # 命中条件与异常语义见 routing/continuation.py::try_travel_pending
        pending_update = try_travel_pending(query, routing_context)
        if pending_update is not None:
            return _with_router_decisions(
                state, _mark_route_from_update(state, pending_update), query,
                existing_override=pending_update,
            )
        # 交易挂起续填（Phase 5 / D2）：预订/比价子图澄清期，用户答纯槽位值
        # （「10月3日」）——两子图无 checkpointer，不拦就掉域。命中条件与
        # 异常语义见 routing/continuation.py::try_booking_pending
        booking_pending = try_booking_pending(query, routing_context)
        if booking_pending is not None:
            return _with_router_decisions(
                state, _mark_route_from_update(state, booking_pending), query,
                existing_override=booking_pending,
            )
        # 延续命中 → 直接回活跃域（travel/cs/selection 有状态域图）
        cont_update = _try_continuation(state, query, routing_context)
        if cont_update is not None:
            return _with_router_decisions(
                state, _mark_route_from_update(state, cont_update), query,
                existing_override=cont_update,
            )
        # 问候/能力咨询 → general_chat 主 LLM 直答（禁 RAG，不进域图）
        general_update = _try_general_chat(state)
        if general_update is not None:
            return _with_router_decisions(
                state, general_update, query, existing_override=general_update,
            )

    # ── redirect_main（2026-09-18）：两阶段判定见 routing/lock_domain.py
    # （正则 → LLM 语义仲裁，默认 OFF）；命中则转出主路由。
    cs_redirect = None
    if cs_forced and not cs_rule_hits:
        cs_redirect = detect_cs_redirect(query)

    if cs_forced and not cs_redirect:
        cs_update = _try_cs_prefilter(query, state, forced=True)
        if cs_update is not None:
            update = _mark_route_from_update(
                state, _enrich_with_understanding(cs_update, query))
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    elif not cs_forced and cs_rule_hits:
        cs_update = _try_cs_prefilter(query, state)
        if cs_update is not None:
            update = _mark_route_from_update(
                state, _enrich_with_understanding(cs_update, query))
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )

    # ── 旅游/选品/预订/商务四连预过滤（域锁且未转出时跳过）─────────
    # 顺序与语义冻结，实现见 routing/prefilter_chain.py::run_domain_prefilters
    if not cs_forced or cs_redirect:
        domain_result = run_domain_prefilters(query, state)
        if domain_result is not None:
            return domain_result

    # ── CS 兜底（全局入口）：无 CS 规则命中时再走一次完整 CS 预过滤 ──
    # 检测器已无向量通道（3f88b4f 删除），与上面的规则预判同源；保留是为
    # 走通 detect_cached + 灰度判定路径，行为与旧版一致。
    if not cs_rule_hits and not cs_forced:
        cs_update = _try_cs_prefilter(query, state)
        if cs_update is not None:
            update = _mark_route_from_update(
                state, _enrich_with_understanding(cs_update, query))
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )

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
        try:
            from backend.orchestration.context.routing_context import (
                set_pending_question,
            )

            set_pending_question(
                state.get("tenant_id") or "", state.get("user_id") or "",
                state.get("session_id") or "",
                clarify.get("question") or "",
            )
        except Exception:
            logger.debug("[RouterNode] 待答问题记录失败（软降级）", exc_info=True)
        update = {
            "route_decision": None,
            "route_mode": "clarify",
            "_clarify": clarify,
        }
        return _with_router_decisions(
            state, update, query, existing_override=update,
        )

    try:
        # 路由引擎初始化单独成 span，避免索引/模型资源初始化成为不可见耗时。
        try:
            from backend.observability.tracer import trace_collector
            init_span = trace_collector.start_span(
                "router_init", name="RoutingEngine 初始化", type="tool_call",
                input={"query_len": len(query)})
            t_init = time.time()
            try:
                engine = get_routing_engine()
            finally:
                trace_collector.end_span(
                    init_span,
                    metrics={"init_ms": int((time.time() - t_init) * 1000)})
        except Exception:
            logger.debug("[RouterNode] router_init span 记录失败", exc_info=True)
            engine = get_routing_engine()
        # 同步调用（主图路由入口是同步的）。上下文完整传入引擎，
        # 由引擎统一决定哪些字段参与路由与缓存，入口不再拼接第二套协议。
        route_context = {
            "department": state.get("department") or "",
            "user_id": state.get("user_id") or "",
            "active_domain": routing_context.get("active_domain") or "",
            "last_intent": routing_context.get("last_intent") or "",
            "last_action": routing_context.get("last_action") or "",
            "brief_summary": routing_context.get("brief_summary") or {},
            "pending_question": routing_context.get("pending_question") or "",
        }
        decision = engine.route(
            query,
            {
                **state,
                "routing_context": route_context,
            },
        )
        logger.info(
            f"[RouterNode] mode={decision.execution_mode.value} "
            f"conf={decision.confidence:.2f} "
            f"cands={[(c.name, c.score) for c in decision.candidates]}"
        )
    except Exception as e:
        logger.warning(f"[RouterNode] 统一路由引擎失败，进入安全澄清: {e}")
        update = {
            "route_decision": None,
            "route_mode": "clarify",
            "router_fallback_reason": f"engine:{type(e).__name__}",
            "legacy_used": False,
        }
        return _with_router_decisions(
            state, update, query, existing_override=update,
        )

    # V2: workflow / direct 不再降级到 plan
    mode = decision.execution_mode

    # ── 统一路由元数据分派 ─────────────────────────────────────
    # RouteDecision.routing_meta 由 RoutingEngine 携带。
    # 消费顺序：域图类域 → 复用既有 prefilter（未放行回到同一引擎）；
    # unknown/低置信 → 澄清；工具域/plan → 决策照常向下，粗分类字段展平入 state。
    # 分派实现见 routing/hierarchical.py::_handle_hierarchical_meta。
    hierarchical_fields: dict = {}
    meta = getattr(decision, "routing_meta", None)
    if meta:
        early = _handle_hierarchical_meta(meta, state, query, route_context)
        if early is not None:
            return _with_router_decisions(
                state,
                early,
                query,
                existing_override=early,
                hierarchical_meta=meta,
            )
        hierarchical_fields = _hierarchical_state_fields(meta)

    # ── QueryRouter 统一问题理解（治理改造 2026-09-22）──────────
    # 纯规则 + 既有路由决策合成（零新增 LLM 调用）；两个消费点：
    #   1. state["query_understanding"] 供 Planner/Reporter/Trace 消费；
    #   2. 简单问题（单能力、无组合措辞）被路由丢给 plan 时降级为 direct，
    #      阻止「SKU 库存多少」这类事实查询白跑 planner→critique→supervisor。
    understanding: dict = {}
    try:
        from backend.orchestration.router.query_understanding import understand_query

        understanding = understand_query(query, decision)
        downgrade = understanding.get("downgrade")
        if downgrade:
            logger.info(
                "[RouterNode] QueryRouter 降级 plan→direct: capability=%s "
                "complexity=%s",
                downgrade.get("capability"),
                understanding.get("complexity"),
            )
            mode = decision.execution_mode.DIRECT
            try:
                from backend.observability.tracer import trace_collector
                t = trace_collector.current()
                if t is not None:
                    t.tags["queryrouter_downgrade"] = str(
                        downgrade.get("capability") or "")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[RouterNode] QueryRouter 理解失败（软降级，保持原路由）: {e}")

    update = {
        "route_decision": decision.model_dump(),
        "route_mode": mode.value,
        "query_understanding": understanding,
        **hierarchical_fields,
        # 历史字段保留 schema 兼容性，但统一引擎路径永远不是旧路由。
        "legacy_used": False,
    }
    return _with_router_decisions(
        state,
        update,
        query,
        existing_override=decision,
        hierarchical_meta=meta,
    )


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
    if mode == "general_chat":
        # 寒暄/能力咨询直答（2026-09-22）：主 LLM 直连，不进 RAG/域图
        return "general_chat"
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get(mode)
    if domain:
        return domain.node_name
    return "planner"
