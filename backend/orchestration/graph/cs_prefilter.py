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


def try_cs_prefilter(query: str, state: dict, forced: bool = False) -> dict | None:
    """CS 域预过滤 + 灰度放量。

    Args:
        forced: 入口域锁（2026-09-18）——客服窗口（CSDrawer）每条消息带
            domain_hint=customer_service。用户已显式进入客服窗口，语义上
            不存在"漏进主图"的实验对照，故跳过域检测门（detect 未命中也进）
            与灰度判定（恒 treatment）；仅保留 CS_ENABLED 总闸与 InputGuard。
            域检测仍照常执行，rule_hits 作为 coarse 分类 hint 参与路由。

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

    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "anonymous")

    # ── 显式触发直通（确定性过滤层，2026-09-17）─────────────────────
    # 转人工类指令（"转人工"/"找真人"/"转接人工客服"…见 handoff.py 关键词表）
    # 是用户的硬性意图，不允许被 embedding 域检测漏判、也不允许落进灰度
    # control 组——否则会像普通模糊查询一样进业务 Agent 的澄清兜底。
    # 零成本：纯正则，无模型调用；确定性：直接合成 human_handoff 路由结果。
    from backend.customer_service.handoff import detect_handoff_trigger
    explicit_trigger = detect_handoff_trigger(query)
    if explicit_trigger is not None:
        cs_result = _build_explicit_handoff_result(explicit_trigger)
        logger.info(
            f"[CsPrefilter] 显式转人工直通: {explicit_trigger.reason} → cs_handoff"
        )
    else:
        # ── 人工接管期强制接管（2026-09-17）─────────────────────────
        # 会话存在未关闭的转接（waiting_human/human_active）时，用户的一切
        # 消息都应转达人工，不允许再被 embedding 域检测漏判漏进主图——
        # 实测接管期间发"我的订单一直没发货"，域检测判非客服，主图向量
        # 路由瞎匹配到 email.search，三次重试失败后回了无关兜底文案。
        # 数据源 HandoffStore（L1 缓存 + DB 回查），异常时按无转接处理。
        relay_state = _active_relay_state(user_id, session_id)
        if relay_state is not None:
            cs_result = _build_explicit_handoff_result(_RelayTrigger(relay_state))
            logger.info(
                f"[CsPrefilter] 人工接管期强制接管: handoff_state={relay_state}"
                f" → cs_handoff"
            )
        else:
            from backend.customer_service.router.domain_detector import detect_cached
            from backend.customer_service.router.cs_router import get_cs_router

            # detect_cached：同 query 5min 内复用检测结果（检测只依赖 query、
            # 与 session 无关），省掉重复请求的云端 embedding 往返（实测 1.0~3.4s）。
            detection = detect_cached(query)
            if not detection.is_cs and not forced:
                return None

            # ── 灰度放量判定（forced 域锁恒 treatment；其余 domain 命中之后判定，
            # 避免对 control 组白烧检测开销之外逻辑）──────────────────────────
            if forced:
                _stamp_variant(session_id, "treatment")
            elif not _in_rollout(session_id):
                _stamp_variant(session_id, "control")
                return None
            else:
                _stamp_variant(session_id, "treatment")

            cs_result = get_cs_router().route(query, detection)

    from backend.observability.metrics import record_cs_intent
    record_cs_intent(cs_result.intent)

    route_path = cs_result.route_path.value

    # P2.2：route_path → cs_target 改查 graph_state 单一事实源（此前 if/elif
    # 与 supervisor/graph_state 的映射各自硬编码，新增 route_path 需改 4 处）
    from backend.customer_service.graph_state import ROUTE_PATH_TO_CS_TARGET
    cs_target = ROUTE_PATH_TO_CS_TARGET.get(route_path, "cs_pending")

    logger.info(
        f"[CsPrefilter] CS 域命中{'(域锁)' if forced else ''}: domain={cs_result.domain.value} "
        f"intent={cs_result.intent} conf={cs_result.confidence:.2f} "
        f"→ {cs_target}"
    )

    # ── Phase 5: CS Input Guard ──
    from backend.customer_service.security.input_guard import get_cs_input_guard
    guard_result = get_cs_input_guard().check(query)
    if guard_result.action.value in {"block", "clarify"}:
        logger.info(
            f"[CsPrefilter] CS InputGuard {guard_result.action.value.upper()}: "
            f"category={guard_result.category.value} reason={guard_result.reason}"
        )
        return {
            "route_decision": None,
            "route_mode": "clarify",
            "final_answer": guard_result.message or "无法处理该客服请求。",
        }

    # ── Trace: stamp conversation_id + cs_route + cs_target（路由一致率用）──
    try:
        from backend.observability.tracer import trace_collector
        trace = trace_collector.current()
        if trace is not None:
            trace.tags["conversation_id"] = session_id
            trace.tags["cs_target"] = cs_target
            if forced:
                # token usage 归因依赖此 tag（proxy._usage_component →
                # component="customer_service"）；同时供 trace 侧区分锁域轮次
                trace.tags["cs_domain_lock"] = "1"
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


class _RelayTrigger:
    """人工接管期合成的 trigger 占位（_build_explicit_handoff_result 只读 .reason）。"""

    def __init__(self, handoff_state: str):
        self.reason = f"human_session_relay: handoff_state={handoff_state}"


def _active_relay_state(user_id: str, session_id: str) -> str | None:
    """会话存在未关闭转接（排队中/人工已接入）时返回其 handoff_state。

    数据源 HandoffStore（L1 缓存优先，miss 回查 DB）；任何异常按无转接
    处理返回 None，不阻断正常路由。
    """
    try:
        from backend.customer_service.handoff_store import get_handoff_store
        data = get_handoff_store().load(user_id, session_id)
        if data and data.get("handoff_state") in ("waiting_human", "human_active"):
            return data["handoff_state"]
    except Exception:
        logger.debug("[CsPrefilter] relay state lookup failed", exc_info=True)
    return None


def _build_explicit_handoff_result(trigger) -> "CSRouteResult":
    """显式转人工 → 直接合成 human_handoff 路由结果（不过 classifier）。

    确定性保证：classify 链路（rule/LLM）再准也有漏判概率，而用户说
    "转人工"时的意图无需推断。conf 固定 1.0，reason 携带触发详情进 trace。
    """
    from backend.customer_service.router.types import (
        CSDomain,
        CSRoutePath,
        CSRouteResult,
    )

    return CSRouteResult(
        domain=CSDomain.HUMAN,
        intent="h_handoff",
        confidence=1.0,
        requires_auth=False,
        requires_action=False,
        risk_level="low",
        route_path=CSRoutePath.HUMAN_HANDOFF,
        kb_ids=[],
        reason=f"explicit_bypass: {trigger.reason}",
    )


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
