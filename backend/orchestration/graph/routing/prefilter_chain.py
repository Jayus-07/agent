"""routing/prefilter_chain.py — 决策组装 + 域 prefilter 链（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留）：
  - _ROUTE_MODE_DOMAIN / _mark_route_from_update：prefilter 命中回写会话路由上下文
  - _with_router_decisions：新适配器决策对象组装（STOP B Router 收口）
  - _try_general_chat：Guard GREETING 直答分流
  - cs_rule_hits_of：CS 廉价规则预判（入口预过滤顺序第一步）
  - _try_cs_prefilter：CS 预过滤薄包装
  - run_domain_prefilters：旅游 → 选品 → 预订 → 商务四连 prefilter

依赖方向（方案冻结）：prefilter_chain 不 import 本包（routing/）其他模块；
被 router_node / hierarchical 消费。唯一新增外部依赖是
``backend.orchestration.domain_registry``（叶子模块：仅依赖 domain_graph +
logger）——用于把回写层口径改为注册表派生（2026-09-30 归属单一事实源化）。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger
from backend.observability.log_privacy import query_preview
from backend.orchestration.domain_registry import (
    DerivedDomainMap,
    domain_graph_registry,
)

# route_mode → ConversationContext.active_domain（预过滤命中回写用）。
# 客服 clarify（route_mode=clarify，出自 cs_prefilter）不在此表，由
# _mark_route_from_update 单独兜底为客服域。
#
# 【派生，零手写】本表内容 = 注册表现有域图（谁注册就登记谁），由
# domain_graph_registry.route_mode_to_active_domain() 现算，不再手工维护。
# 历史教训（Phase 5 / D2 G1）：手写期漏了 travel_booking / travel_commerce →
# prefilter 命中取 domain=None → mark_domain_turn 早退 → active_domain 永不写 →
# 下一轮「10月3日」这类纯槽位值回答既拉不回也无人续填（两跳断片）。
# 派生后「漏登记」在结构上不可能发生；活视图保证不因注册时序而拿到空表。
_ROUTE_MODE_DOMAIN = DerivedDomainMap(
    domain_graph_registry.route_mode_to_active_domain,
    label="_ROUTE_MODE_DOMAIN",
)


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
    """把统一路由引擎结果增量写回 state，兼容字段仍保持可序列化。

    该函数只做决策对象组装，不执行 Tool/Skill/Workflow；prefilter 的调用顺序
    仍由 ``router_node`` 原有分支控制。任何适配器异常都软失败，不阻断旧路径。
    """

    result = {**state, **(update or {})}
    try:
        from backend.orchestration.router.capability_router import CapabilityRouter
        from backend.orchestration.router.domain_router import DomainRouter
        from backend.orchestration.router.execution_mode import ExecutionModeResolver
        from backend.orchestration.router.intent_router import IntentRouter
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
            # 统一引擎已经完成决策，不重复做一次 embedding。
            domain_decision = {
                "domain": "unknown",
                "subflow": None,
                "confidence": 0.0,
                "source": "route_engine",
                "reasoning": "RouteDecision 已由统一引擎完成",
            }
        else:
            domain_decision = domain_router.route(
                query, state, prefilter_update=update,
            )

        intent_decision = IntentRouter().classify(
            query,
            domain_decision,
            existing_override=existing_override,
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
        # 历史字段保留以兼容 checkpoint，但统一引擎不再写入 True。
        current_legacy_used = False
        result.update({
            "domain_decision": domain_decision,
            "intent_decision": intent_decision,
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
            intent_decision,
        )
    except Exception as exc:
        logger.warning("[RouterNode] 路由决策适配器失败，保持兼容字段: %s", exc)
        result.update({
            "router_fallback_reason": f"decision_adapter:{exc}",
            "legacy_used": False,
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


# ═══════════════════════════════════════════════════════════════════
# 域入口模式（多域隔离收官 M2，2026-10-06）────────────────────────────
# 拍板判据：一次性答案（主图现能力可答完，如 poi_search/map.lookup）留
# 主图直答；产出作品（行程/漏斗报告/工单流）经 handoff 契约引导去专属页。
# 每域一个入口模式开关（sys_config 登记，DB 覆盖免重启），默认 execute =
# 行为零变化；域总闸（CS_ENABLED 等）关闭时 prefilter 本就不命中，模式
# 开关只在「命中后」决定执行还是引导。
# 域锁入口（CSDrawer domain_hint / 旅游页直达端点）不经过本判据。
# ═══════════════════════════════════════════════════════════════════

# route_mode → 域族（travel 家族三域图共用旅游页这一个专属入口）
_ROUTE_MODE_FAMILY: dict[str, str] = {
    "travel": "travel",
    "travel_booking": "travel",
    "travel_commerce": "travel",
    "customer_service": "customer_service",
    "selection_funnel": "selection_funnel",
}

_ENTRY_MODE_SWITCHES: dict[str, str] = {
    "travel": "TRAVEL_GLOBAL_ENTRY_MODE",
    "customer_service": "CS_GLOBAL_ENTRY_MODE",
    "selection_funnel": "SELECTION_GLOBAL_ENTRY_MODE",
}

# guide 模式下旅游族的一次性查询豁免：命中这些「单点查询」语义时不引导，
# 落回主路由由 travel.poi_search / map.lookup / rag 直答（判据左半边）。
# 规划/多日/路线类语义不在表内 → 照常引导（判据右半边）。
_ONE_SHOT_TRAVEL_RE = re.compile(
    r"有什么景点|景点推荐|哪些景点|门票|开放时间|好玩吗|值得去吗|值得玩吗|"
    r"在哪|怎么去|距离多远|人均|评分"
)


def domain_entry_mode(route_mode: str) -> str:
    """route_mode 所属域的入口模式（execute | guide）；不可判一律 execute。"""
    family = _ROUTE_MODE_FAMILY.get(route_mode or "")
    if not family:
        return "execute"
    try:
        from backend.services import sys_config
        return sys_config.get_mode(_ENTRY_MODE_SWITCHES[family]) or "execute"
    except Exception:
        return "execute"


def entry_mode_verdict(query: str, update: dict | None) -> str:
    """prefilter 命中后的三态判定（execute | guide | passthrough）。

    - execute：按既有路径进域图（默认模式，行为零变化）；
    - guide：产 handoff 更新引导去专属页；
    - passthrough：本次命中作废（旅游一次性查询），调用方按未命中继续。
    cs_prefilter 的业务门禁短路（route_mode=clarify）等非域图归宿恒 execute。
    """
    if not isinstance(update, dict):
        return "execute"
    route_mode = str(update.get("route_mode") or "")
    if route_mode not in _ROUTE_MODE_FAMILY:
        return "execute"
    if domain_entry_mode(route_mode) != "guide":
        return "execute"
    if (_ROUTE_MODE_FAMILY[route_mode] == "travel"
            and _ONE_SHOT_TRAVEL_RE.search(query or "")):
        return "passthrough"
    return "guide"


def handoff_update_for(query: str, state: dict, route_mode: str) -> dict:
    """构造 handoff 路由更新（契约体 + 短路归宿；不回写路由上下文）。

    注意：handoff 轮**不做** _mark_route_from_update——域并未真正开始，
    回写 active_domain 会让下一轮「3天」这类短指令被 ContinuationResolver
    拉进一个从未执行的域。
    """
    from backend.orchestration.contracts.handoff import build_handoff_payload

    family = _ROUTE_MODE_FAMILY.get(route_mode, route_mode)
    payload = build_handoff_payload(family, query).model_dump()
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is not None:
            trace.tags["handoff_target_domain"] = family
    except Exception:  # noqa: BLE001 — 观测旁路
        pass
    logger.info(
        "[PrefilterChain] 域引导(guide 模式): family=%s query=%s",
        family, query_preview(query),
    )
    return {
        "route_decision": None,
        "route_mode": "handoff",
        "_handoff": payload,
    }


def _finish_prefilter_hit(state: dict, query: str, update: dict) -> dict | None:
    """prefilter 命中出口的统一收口（M2 判据接线，行为按模式分派）。

    返回 None = passthrough（调用方继续下一个 prefilter / 后续链路）。
    """
    verdict = entry_mode_verdict(query, update)
    if verdict == "passthrough":
        return None
    if verdict == "guide":
        handoff = handoff_update_for(
            query, state, str(update.get("route_mode") or ""))
        return _with_router_decisions(
            state, handoff, query, existing_override=handoff,
        )
    update = _mark_route_from_update(state, update)
    return _with_router_decisions(
        state, update, query, existing_override=update,
    )


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
    M2（2026-10-06）：命中出口统一经 _finish_prefilter_hit 按域入口模式
    分派执行/引导；guide 模式下旅游一次性查询 passthrough 继续后续链路。
    """
    try:
        from backend.orchestration.graph.travel_prefilter import try_travel_prefilter
        travel_update = try_travel_prefilter(query, state)
        if travel_update is not None:
            result = _finish_prefilter_hit(state, query, travel_update)
            if result is not None:
                return result
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
            result = _finish_prefilter_hit(state, query, funnel_update)
            if result is not None:
                return result
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
            result = _finish_prefilter_hit(state, query, booking_update)
            if result is not None:
                return result
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
            result = _finish_prefilter_hit(state, query, commerce_update)
            if result is not None:
                return result
    except Exception as e:
        logger.warning(f"[RouterNode] 商务预过滤失败，回退到主 Router: {e}")

    return None
