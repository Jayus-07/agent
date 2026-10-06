"""routing/hierarchical.py — 分层路由决策分派（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留；方案四模块之外新增——
router_node.py 在方案成文后增长出的分层路由块，拆分时必须有着落）：
  - _hierarchical_state_fields：routing_meta → AgentState 平铺字段
  - _handle_hierarchical_meta：统一路由决策分派（prefilter 复用/澄清/引擎续判）

依赖方向（单向）：hierarchical → prefilter_chain + cs_understanding，不回环。
"""
from __future__ import annotations

from backend.orchestration.graph.routing.cs_understanding import (
    _enrich_with_understanding,
)
from backend.orchestration.graph.routing.prefilter_chain import (
    _mark_route_from_update,
    entry_mode_verdict,
    handoff_update_for,
)
from backend.orchestration.router import get_routing_engine
from backend.orchestration.router.projection import (
    build_route_decision_v2_from_engine,
    project_route_decision_to_legacy_state,
    route_update_for_mode,
)
from backend.shared.logger import logger


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
        "intent_decision": {
            "intent": meta.get("intent") or "",
            "kind": meta.get("intent_kind") or "",
            "confidence": float(meta.get("intent_confidence") or 0.0),
            "source": meta.get("intent_source") or "",
            "reasoning": meta.get("intent_reasoning") or "",
            "execution_hint": meta.get("intent_execution_hint"),
            "candidate_names": list(meta.get("intent_candidates") or []),
        },
    }


def _handle_hierarchical_meta(meta: dict, state: dict, query: str,
                              route_context: dict) -> dict | None:
    """hierarchical 决策分派。

    Returns:
        dict → 提前返回的 state 更新（prefilter 命中 / 澄清 / 引擎续判）；
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
            **route_update_for_mode(
                "general_chat",
                extra={"route_decision": None, **fields},
            ),
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
            logger.warning(f"[RouterNode] 域图 prefilter 失败，继续走统一路由引擎: {e}")
        if update is not None:
            # 多域隔离 M2（2026-10-06）：guide 模式下命中 → handoff 引导，
            # 旅游一次性查询 passthrough → 交回统一引擎（判据左半边兜底）。
            verdict = entry_mode_verdict(query, update)
            if verdict == "guide":
                handoff = handoff_update_for(
                    query, state, str(update.get("route_mode") or ""))
                return {**state, **handoff}
            if verdict == "execute":
                return {**state, **_mark_route_from_update(
                    state, _enrich_with_understanding(update, query))}
        if update is None or entry_mode_verdict(query, update) == "passthrough":
            # 未放行（如 CS 灰度 control 组 / 检测器不同意 / guide 模式一次性查询）
            # → 交回统一引擎。
            # 这里不能再调用旧 Router 兼容方法，否则一次请求会重新进入
            # 另一套路由算法，导致结果来源不明、故障语义不一致。
            engine_state = {
                **state,
                "routing_context": route_context,
            }
            engine_decision = get_routing_engine().route(query, engine_state)
        engine_route_mode = (
            engine_decision.route_mode or engine_decision.execution_mode.value
        )
        v2_decision = build_route_decision_v2_from_engine(
            engine_decision,
            route_mode=engine_route_mode,
            domain_meta=meta,
        )
        return {
            **state,
            **project_route_decision_to_legacy_state(
                v2_decision,
                legacy_route_mode=engine_route_mode,
                legacy_route_decision=engine_decision.model_dump(),
            ),
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
            if clarify_allowed(state.get("session_id", ""), query):
                clarify = build_refusal_clarify(query, state.get("domain_hint") or "")
                mark_clarified(
                    state.get("session_id", ""), query,
                    options=clarify.get("options"),
                    source=clarify.get("source", ""),
                )
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
                    **route_update_for_mode(
                        "clarify",
                        extra={
                            "route_decision": None,
                            "_clarify": clarify,
                            **fields,
                            "need_clarification": True,
                        },
                    ),
                }
        except Exception as e:
            logger.warning(f"[RouterNode] 路由澄清构造失败，继续走统一 plan 支线: {e}")
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
