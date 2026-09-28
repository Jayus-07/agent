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
    except Exception as exc:
        logger.warning("[RouterNode] Router 决策适配器失败，保持旧字段: %s", exc)
        result.update({
            "router_fallback_reason": f"decision_adapter:{exc}",
            "legacy_used": True,
        })
    return result


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

    # ── 路由入口重构（2026-09-22）：Guard → Context Assembler →
    # ContinuationResolver → Coarse Domain Router ──────────────────
    # routing_context 由 runner 在图外组装（assemble_routing_context），
    # 缺省空 dict = 无活跃任务，延续判定自动失效。
    routing_context = state.get("routing_context") or {}
    if not cs_forced:
        # Travel Pending Resolver（STOP F2，先于 ContinuationResolver）：
        # 活跃 travel 任务有结构化 pending 时，纯槽位值回答（「8万日元」
        # 「住难波」）短路回旅游域——这类话既无旅游信号也无延续信号，
        # 不拦就丢上下文（追问没人接住）。命中条件与 NEW_RUN 判定见
        # travel_pending_resolver 模块 docstring；客服强信号在其内部放行。
        try:
            from backend.orchestration.context.travel_pending_resolver import (
                resolve_travel_pending,
            )

            pending_update = resolve_travel_pending(query, routing_context)
            if pending_update is not None:
                return _with_router_decisions(
                    state, _mark_route_from_update(state, pending_update), query,
                    existing_override=pending_update,
                )
        except Exception as e:
            logger.warning(f"[RouterNode] travel pending 判定失败，走正常路由: {e}")
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
            update = _mark_route_from_update(
                state, _enrich_with_understanding(cs_update, query))
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )
    elif not cs_forced and cs_rule_hits:
        cs_update = _try_cs_prefilter()
        if cs_update is not None:
            update = _mark_route_from_update(
                state, _enrich_with_understanding(cs_update, query))
            return _with_router_decisions(
                state, update, query, existing_override=update,
            )

    # ── 旅游预过滤：纯正则 ─────────────────────────────────────────
    # 域锁且未转出时跳过；转出（redirect_main）或全局入口正常执行。
    if not cs_forced or cs_redirect:
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

    # ── CS 兜底（全局入口）：无 CS 规则命中时再走一次完整 CS 预过滤 ──
    # 检测器已无向量通道（3f88b4f 删除），与上面的规则预判同源；保留是为
    # 走通 detect_cached + 灰度判定路径，行为与旧版一致。
    if not cs_rule_hits and not cs_forced:
        cs_update = _try_cs_prefilter()
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
        # context 进入路由缓存键：同文 query 在不同 user/department 下不会
        # 互串缓存。2026-09-22 起额外携带会话任务状态（active_domain 等），
        # 供粗分类器/HierarchicalRouter 消费（纯规则延续判定已在上方完成，
        # 这里是 Router 层的上下文可见性）。
        route_context = {
            "department": state.get("department") or "",
            "user_id": state.get("user_id") or "",
            "active_domain": routing_context.get("active_domain") or "",
            "last_intent": routing_context.get("last_intent") or "",
            "last_action": routing_context.get("last_action") or "",
            "brief_summary": routing_context.get("brief_summary") or {},
            "pending_question": routing_context.get("pending_question") or "",
        }
        decision = router.route(query, context=route_context)
        logger.info(
            f"[RouterNode] mode={decision.execution_mode.value} "
            f"conf={decision.confidence:.2f} "
            f"cands={[(c.name, c.score) for c in decision.candidates]}"
        )
    except Exception as e:
        logger.warning(f"[RouterNode] 路由失败，回退到 plan: {e}")
        update = {
            "route_decision": None,
            "route_mode": "plan",
            "router_fallback_reason": f"router:{e}",
            "legacy_used": True,
        }
        return _with_router_decisions(
            state, update, query, existing_override=update,
        )

    # V2: workflow / direct 不再降级到 plan
    mode = decision.execution_mode

    # ── 分层路由（hierarchical routing，2026-09-22）──────────────
    # RouteDecision.routing_meta 由 hierarchical 模式携带；legacy 恒 None。
    # 消费顺序：域图类域 → 复用既有 prefilter（未放行回退 legacy 路由）；
    # unknown/低置信 → 澄清；工具域/plan → 决策照常向下，粗分类字段展平入 state。
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
        "legacy_used": meta is None,
    }
    return _with_router_decisions(
        state,
        update,
        query,
        existing_override=decision,
        hierarchical_meta=meta,
    )


def _hierarchical_state_fields(meta: dict) -> dict:
    """routing_meta → AgentState 平铺字段（§12，全部可序列化标量/简单容器）。"""
    return {
        "domain": meta.get("domain") or "",
        "domain_confidence": float(meta.get("domain_confidence") or 0.0),
        "domain_margin": float(meta.get("domain_margin") or 0.0),
        "domain_source": meta.get("domain_source") or "",
        "candidate_tools": list(meta.get("candidate_tools") or []),
        "selected_tool": meta.get("selected_tool") or "",
        "tool_arguments": None,  # FC 填参后由 tool_selector 写 resolved_params
        "tool_confidence": float(meta.get("fine_top1_score") or 0.0),
        "tool_route_mode": meta.get("tool_route_mode") or "",
        "need_clarification": bool(meta.get("need_clarification")),
        "clarification_reason": meta.get("clarification_reason") or "",
    }


def _handle_hierarchical_meta(meta: dict, state: dict, query: str,
                              route_context: dict) -> dict | None:
    """hierarchical 决策分派。

    Returns:
        dict → 提前返回的 state 更新（prefilter 命中 / 澄清 / legacy 回退）；
        None → 决策照常向下（plan / tool_route），粗分类字段由调用方并入。
    """
    action = meta.get("domain_action") or "plan"
    fields = _hierarchical_state_fields(meta)

    # rule override（workflow / 复合意图）：无粗分类字段，沿用既有决策路径
    if not fields["domain"]:
        return None

    # ── general 域：寒暄直答（2026-09-22，主 LLM 直连，禁 RAG/工具）──
    if action == "general_chat":
        try:
            from backend.orchestration.context.routing_context import mark_domain_turn

            mark_domain_turn(
                state.get("tenant_id") or "", state.get("user_id") or "",
                state.get("session_id") or "",
                domain="general", intent="general_chat", action="general_chat",
                pending_question="",
            )
        except Exception:
            pass
        return {
            **state,
            "route_decision": None,
            "route_mode": "general_chat",
            **fields,
        }

    # ── 域图类域：复用既有 prefilter（不新增路由实现）─────────────
    if action.startswith("prefilter_"):
        update = None
        try:
            if action == "prefilter_cs":
                from backend.orchestration.graph.cs_prefilter import try_cs_prefilter
                update = try_cs_prefilter(query, state)
            elif action == "prefilter_travel":
                from backend.orchestration.graph.travel_prefilter import try_travel_prefilter
                update = try_travel_prefilter(query, state)
            elif action == "prefilter_selection":
                from backend.orchestration.graph.selection_funnel_prefilter import (
                    try_selection_funnel_prefilter,
                )
                update = try_selection_funnel_prefilter(query, state)
        except Exception as e:
            logger.warning(f"[RouterNode] 分层路由域图 prefilter 失败，回退 legacy: {e}")
        if update is not None:
            return {**state, **_mark_route_from_update(
                state, _enrich_with_understanding(update, query))}
        # 未放行（如 CS 灰度 control 组 / 检测器不同意）→ legacy 路由重新决策；
        # route_legacy 绕过 hierarchical 分支与缓存，粗分类字段保留供评测
        from backend.orchestration.router import get_router
        legacy_decision = get_router().route_legacy(query, route_context)
        return {
            **state,
            "route_decision": legacy_decision.model_dump(),
            "route_mode": legacy_decision.execution_mode.value,
            **fields,
        }

    # ── unknown / 低置信：澄清（复用 L1 追问卡片与防循环守卫）─────
    if action == "clarify":
        try:
            from backend.orchestration.graph.clarify_content import (
                build_refusal_clarify,
                clarify_allowed,
                mark_clarified,
            )
            if clarify_allowed(state.get("session_id", "")):
                clarify = build_refusal_clarify(query, state.get("domain_hint") or "")
                mark_clarified(state.get("session_id", ""))
                logger.info(
                    f"[RouterNode] 分层路由澄清: reason={fields['clarification_reason']}"
                )
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
                    pass
                return {
                    **state,
                    "route_decision": None,
                    "route_mode": "clarify",
                    "_clarify": clarify,
                    **fields,
                    "need_clarification": True,
                }
        except Exception as e:
            logger.warning(f"[RouterNode] 分层路由澄清构造失败，回退 plan 支线: {e}")
        return None  # 防循环守卫不放行 / 构造失败 → 照常走 plan 支线

    # ── 工具域 / plan 拍板：回写路由上下文（下一轮延续判定数据源）──
    if action in ("tool_route", "plan"):
        try:
            from backend.orchestration.context.routing_context import mark_domain_turn

            mark_domain_turn(
                state.get("tenant_id") or "", state.get("user_id") or "",
                state.get("session_id") or "",
                domain=fields["domain"], intent=action,
                action=fields["selected_tool"] or fields["domain"],
                pending_question="",
            )
        except Exception:
            logger.debug("[RouterNode] 工具域路由上下文回写失败（软降级）",
                         exc_info=True)

    # plan / tool_route：决策照常向下（direct → tool_selector → skill_executor）
    return None


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
