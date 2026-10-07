"""routing/lock_domain.py — 客服窗口锁域 + redirect_main 转出（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，语句逐字保留）：
  - is_cs_forced：domain_hint=customer_service/cs 的锁域判定
  - detect_cs_redirect：redirect_main 两阶段（确定性正则 → LLM 语义仲裁）
"""
from __future__ import annotations

from backend.shared.logger import logger

# 入口域锁 hint 集合（CSDrawer 每条消息带 domain_hint=customer_service）
CS_FORCED_HINTS = ("customer_service", "cs")


def is_cs_forced(state: dict) -> bool:
    """客服窗口锁域判定（原 router_node 主函数内联两行）。

    用户已显式进入客服窗口，若每条消息重新判域，非客服问法会被甩到
    主图 plan 支线白烧 LLM；且 CS 规则阈值 CS_RULE_MIN_HITS=2 漏掉
    "东西坏了咋办"这类高频问法（实测命中仅 1）。锁域强制走 CS 预过滤。
    """
    domain_hint = (state.get("domain_hint") or "").strip().lower()
    return domain_hint in CS_FORCED_HINTS


# AI 助手页（主聊天 /agent）锁域 hint 集合：前端 useChat 每条消息带
# domain_hint=main（与 CS 抽屉带 customer_service 同机制、互斥取值）
MAIN_HINTS = ("main", "agent")


def is_main_forced(state: dict) -> bool:
    """AI 助手页锁域判定（2026-10-08 产品拍板：隔离+引导卡）。

    AI 助手 = 独立通用助手：跳过旅游/选品/预订/商务域 prefilter 与旅游
    延续判定，域请求不在本页执行；旅游族/选品强信号改产 handoff 引导卡
    送用户去专属页。CS 引导、general_chat 与主路由（sql/rag/plan）不受影响。
    """
    domain_hint = (state.get("domain_hint") or "").strip().lower()
    return domain_hint in MAIN_HINTS


def main_forced_handoff_update(query: str, state: dict) -> dict | None:
    """AI 助手锁域下的强信号引导卡：命中返回 handoff 路由更新，否则 None。

    优先级与全局 prefilter 一致（旅游 > 选品 > 预订/商务）；预订/商务
    归旅游族（commerce 为旅游子流），一并引导去旅游页。纯判定零副作用
    ——不复用 run_domain_prefilters：其命中路径会经 _finish_prefilter_hit
    回写路由上下文，与 handoff 语义「域并未真正开始」冲突（见
    handoff_update_for docstring）。
    """
    target: str | None = None
    try:
        from backend.orchestration.graph.travel_prefilter import is_travel_request
        from backend.orchestration.graph.selection_funnel_prefilter import (
            is_selection_funnel_request,
        )
        from backend.orchestration.graph.booking_prefilter import is_booking_request
        from backend.orchestration.graph.commerce_prefilter import is_commerce_request

        if is_travel_request(query):
            target = "travel"
        elif is_selection_funnel_request(query):
            target = "selection_funnel"
        elif is_booking_request(query) or is_commerce_request(query):
            target = "travel"
    except Exception as e:
        logger.debug(f"[LockDomain] main 锁域强信号判定失败，按无信号处理: {e}")
        return None
    if target is None:
        return None

    from backend.orchestration.contracts.handoff import build_handoff_payload
    from backend.orchestration.router.projection import route_update_for_mode

    payload = build_handoff_payload(
        target, query, reason="main_lock_domain_guide",
    ).model_dump()
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is not None:
            trace.tags["handoff_target_domain"] = target
            trace.tags["main_lock_guide"] = "true"
    except Exception:  # noqa: BLE001 — 观测旁路
        pass
    logger.info("[LockDomain] main 锁域引导: target=%s query=%s", target, query[:48])
    return route_update_for_mode(
        "handoff",
        extra={"route_decision": None, "_handoff": payload},
    )


def detect_cs_redirect(query: str) -> str | None:
    """redirect_main 两阶段判定（原 router_node 主函数 ~438-471 段）。

    阶段一（2026-09-18，确定性正则）：域锁下"明显非客服"的问法转出主路由：
    无任何客服规则信号，且命中旅游/选品强信号 → 不进 CS，放行后续
    prefilter 自然路由，抽屉内也能拿到旅游/选品的正常回答（设计稿第 4 节
    next_action=redirect_main 的确定性子集）。混合信号（如"订单里的行程单
    怎么退款"含客服规则）仍守 CS 优先——与主路由既有判定一致，避免旅游
    关键词抢走客服流量。
    阶段二（LLM 语义仲裁）：正则未命中时交给 non_cs 检测器判
    non_cs_confidence，≥阈值才转出。默认 OFF（CS_REDIRECT_MAIN_LLM_ENABLED）。
    返回 None = 维持锁域；非 None = 转出原因（含 trace tag 副作用）。
    """
    cs_redirect = None
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
    return cs_redirect
