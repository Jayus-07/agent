"""cs_prefilter.py — Router 内的客服域预过滤（从 router_node.py 抽出，逻辑不变）

职责：在进入主 Router（rule/vector/LLM 三层路由）之前，用廉价检测判断
当前 query 是否属于客服域。命中则短路返回 CS 路由结果，避免客服流量
烧 LLM 路由耗时；未命中（或 CS 功能关闭/检测异常）返回 None，继续走主 Router。

契约：
  - 只回答"是不是客服问题"，不回答"走哪个 expert"（后者是 CS Graph
    内部 supervisor 的职责，见 customer_service/graph_builder.py）。
  - 任何异常向上抛出由调用方兜底回退主 Router。
"""
from __future__ import annotations

from backend.shared.logger import logger


def try_cs_prefilter(query: str, state: dict) -> dict | None:
    """CS 域预过滤 + 灰度放量。

    Returns:
        命中客服域且在放量范围内 → 返回主图 state 更新 dict（route_mode=
        "customer_service"，含 cs_context；InputGuard 拦截时额外带 final_answer）。
        未命中客服域 / CS 关闭 / 未命中灰度 → None（走主图 = control 组）。
    """
    try:
        from backend.config.customer_service import CS_ENABLED
        if not CS_ENABLED:
            return None
    except Exception:
        return None

    from backend.customer_service.router.domain_detector import get_domain_detector
    from backend.customer_service.router.cs_router import get_cs_router

    detection = get_domain_detector().detect(query)
    if not detection.is_cs:
        return None

    # ── 灰度放量判定（domain 命中之后，避免对 control 组白烧检测开销之外逻辑）──
    session_id = state.get("session_id", "default")
    if not _in_rollout(session_id):
        _stamp_variant(session_id, "control")
        return None
    _stamp_variant(session_id, "treatment")

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
        f"[CsPrefilter] CS 域命中: domain={cs_result.domain.value} "
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
            f"[CsPrefilter] CS InputGuard BLOCK: "
            f"category={guard_result.category.value} reason={guard_result.reason}"
        )
        return {
            "route_decision": None,
            "route_mode": "customer_service",
            "cs_context": _build_cs_context(cs_result, cs_target, user_id, session_id),
            "final_answer": guard_result.message,
        }

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
        "route_decision": None,
        "route_mode": "customer_service",
        "cs_context": _build_cs_context(cs_result, cs_target, user_id, session_id),
    }


def _build_cs_context(cs_result, cs_target: str,
                      authenticated_user_id: str, session_id: str) -> dict:
    from backend.customer_service.context import build_cs_context
    return build_cs_context(
        cs_route=cs_result.model_dump(),
        cs_target=cs_target,
        authenticated_user_id=authenticated_user_id,
        session_id=session_id,
    )


def _in_rollout(session_id: str) -> bool:
    """灰度判定：白名单优先，其次 session_id 稳定哈希百分比。

    用 md5 而非内置 hash——Python hash 有随机盐，进程重启会改变分组。
    """
    from backend.config.customer_service import (
        CS_ROLLOUT_PERCENT, CS_ROLLOUT_WHITELIST,
    )
    if session_id in CS_ROLLOUT_WHITELIST:
        return True
    if CS_ROLLOUT_PERCENT >= 100:
        return True
    digest = int(__import__("hashlib").md5(session_id.encode()).hexdigest(), 16)
    return (digest % 100) < CS_ROLLOUT_PERCENT


def _stamp_variant(session_id: str, variant: str) -> None:
    """trace 打标实验组别（treatment=CS graph / control=主图），供对比评测。"""
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is not None:
            trace.tags["cs_variant"] = variant
    except Exception:
        pass
