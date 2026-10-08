"""
customer_service/supervisor.py — CS Supervisor 决策引擎

职责: 结合状态 + 上下文决定下一步调用哪个 Expert。
不做: 不执行业务逻辑、不操作 DB、不生成最终回复。

两套决策顺序，按 CS_DECISION_V2 分发（默认 v2）:
  v2 — 七层固定优先级（设计方案 §4.2）: handoff → pending → 风险 →
       循环预算 → 意图路由 → 低置信处理 → LLM 兜底（_decision_v2）
  v1 — 存量三层顺序，回退开关用（_decision_v1）

decision_layer 度量口径不变: {1: rule, 2: combination, 3: llm}。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §5.3
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, TypedDict

from langgraph.types import Command

from backend.customer_service.prompting import render_prompt
from backend.shared.logger import logger


class ExpertAction(str, Enum):
    """Supervisor 可下发的动作类型"""
    RUN_EXPERT = "run_expert"
    FINISH = "finish"
    HANDOFF = "handoff"
    PENDING = "pending"


class ExpertType(str, Enum):
    """5 个 Expert Agent"""
    KNOWLEDGE = "knowledge"
    QUERY = "query"
    ACTION = "action"
    COMPLAINT = "complaint"
    HANDOFF = "handoff"


class CSSupervisorDecision(TypedDict, total=False):
    """Supervisor 决策输出"""
    next_action: str
    next_expert: str | None
    decision_layer: int
    reason: str
    requires_confirmation: bool
    requires_handoff: bool
    is_finished: bool
    context_updates: dict
    # T6（2026-10-04 对话体验改造）：分诊直出话术——出域固定话术/寒暄人设
    # 回复。仅 FINISH 终态决策携带，cs_reporter._assemble_answer 直出，
    # 不进任何 expert（reporter 消费后即弃，不落 expert_history）。
    direct_reply: str


# P2.2：route_path / domain → expert 映射统一到 graph_state 单一事实源
# （此前本文件、cs_prefilter、run_supervisor_node 各自硬编码，存在漂移风险）
from backend.customer_service.graph_state import (
    DOMAIN_TO_EXPERT_NAME as _DOMAIN_TO_EXPERT,
    EXPERT_NAME_TO_NODE as _EXPERT_NAME_TO_NODE,
    ROUTE_PATH_TO_EXPERT_NAME as _ROUTE_PATH_TO_EXPERT,
)


def _make_decision(
    action: ExpertAction,
    expert: ExpertType | None,
    layer: int,
    reason: str,
    *,
    requires_confirmation: bool = False,
    requires_handoff: bool = False,
    is_finished: bool = False,
) -> CSSupervisorDecision:
    return CSSupervisorDecision(
        next_action=action.value,
        next_expert=expert.value if expert else None,
        decision_layer=layer,
        reason=reason,
        requires_confirmation=requires_confirmation,
        requires_handoff=requires_handoff,
        is_finished=is_finished,
        context_updates={},
    )


def _resolve_expert(cs_route: dict) -> str:
    """从 cs_route 解析目标 expert — route_path 优先，domain 兜底。"""
    route_path = cs_route.get("route_path", "")
    if route_path and route_path in _ROUTE_PATH_TO_EXPERT:
        return _ROUTE_PATH_TO_EXPERT[route_path]

    domain = cs_route.get("domain", "KNOWLEDGE")
    return _DOMAIN_TO_EXPERT.get(domain, ExpertType.KNOWLEDGE.value)


def make_supervisor_decision(state: dict[str, Any]) -> CSSupervisorDecision:
    """Supervisor 决策入口 —— 按 CS_DECISION_V2 分发。

    v2（默认）：七层固定优先级（设计方案 §4.2，见 _decision_v2）；
    v1（CS_DECISION_V2=false 本机回退）：存量三层顺序，语义保留不动。
    """
    from backend.config.customer_service import CS_DECISION_V2

    if CS_DECISION_V2:
        return _decision_v2(state)
    return _decision_v1(state)


def _decision_v1(state: dict[str, Any]) -> CSSupervisorDecision:
    """存量决策顺序（v1，CS_DECISION_V2=false 回退用）。

    1a handoff 拦截 → 1b loop guard → 2a pending → B5 信号门 →
    2b 重复检测 → 1c 低置信降级 → L3 LLM → 默认路由。
    """
    cs_route = state.get("cs_route", {})
    confidence = cs_route.get("confidence", 0.0)
    handoff_state = state.get("handoff_state", "ai_active")
    confirmation_state = state.get("confirmation_state", "not_required")
    expert_loop_count = state.get("expert_loop_count", 0)
    expert_history = state.get("expert_history", [])

    from backend.config.customer_service import (
        CS_CONFIDENCE_CAUTIOUS,
        CS_EXPERT_MAX_LOOPS,
    )

    # ── Layer 1a: handoff 拦截 ──
    from backend.customer_service.handoff import HandoffState, should_intercept
    try:
        _hs = HandoffState(handoff_state) if handoff_state else HandoffState.AI_ACTIVE
    except ValueError:
        # STOP CS-A P0-6：与 v2 L1 同口径 —— 未知状态 fail loud（078 CHECK
        # 已挡写入端），静默吞成 ai_active 会把脏数据当正常服务。
        logger.error(
            "[CS Supervisor][v1] unknown handoff_state=%r —— fail loud",
            handoff_state,
        )
        raise
    if should_intercept(_hs):
        decision = _make_decision(
            ExpertAction.HANDOFF, ExpertType.HANDOFF, layer=1,
            reason=f"handoff 拦截: state={handoff_state}",
            requires_handoff=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 1b: loop guard ──
    if expert_loop_count >= CS_EXPERT_MAX_LOOPS:
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=f"expert 循环达上限 ({CS_EXPERT_MAX_LOOPS})",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 2a: confirmation pending ──
    if confirmation_state in ("pending", "pending_confirmation"):
        decision = _make_decision(
            ExpertAction.PENDING, None, layer=2,
            reason="confirmation pending — 等待用户确认",
            requires_confirmation=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 1 信号兜底（迁移 B5，2026-09-29）：understanding 信号消费 ──
    # 信号源 = router_node._enrich_with_understanding 写入 cs_route.metadata
    # 的规则信号（零 LLM）；metadata 缺字段（旧状态/直测）时整段跳过，
    # 行为与接线前一致。定位是「兜底」：注入/越权的主拦截在图前 Input Guard。
    # 位置刻意在 2a pending 之后——确认等待中的会话不被信号门改道。
    from backend.config.customer_service import CS_SIGNAL_GATE_ENABLED
    if CS_SIGNAL_GATE_ENABLED:
        metadata = cs_route.get("metadata") or {}
        risk_hits = list(metadata.get("risk_hits") or [])

        # 风险兜底拦截：越权/注入信号 → 拒答收尾（附转人工建议）。
        # 确定性拒绝优先于一切执行类分支（含下方 P0 直通）。
        if risk_hits:
            decision = _make_decision(
                ExpertAction.FINISH, None, layer=1,
                reason=(
                    f"风险信号兜底拦截: {','.join(risk_hits[:3])} — "
                    "拒答+建议转人工"
                ),
                is_finished=True,
            )
            _record_decision(decision)
            try:  # M12：拒答计数（/domain-ops 指标，软失败）
                from backend.observability.metrics import cs_rejection_total
                cs_rejection_total.labels(layer="risk_gate").inc()
            except Exception:
                pass
            return decision

        # P0 投诉直通：监管/舆情信号（12315/曝光/报警）不再依赖路由置信度，
        # 强制派 complaint expert（防重入与升级转人工为该专家既有链路）。
        # 直通仅限本轮 complaint 未执行过——信号是静态的，不设防重入会
        # 压过 2b 重复检测形成 supervisor⇄complaint 死循环（B6 实机验证
        # 暴露的 GraphRecursionError，与 v2 L3 同缺陷，两处同修）。
        if metadata.get("sentiment_hits"):
            from backend.customer_service.understanding.signals import (
                is_p0_escalation,
            )

            complaint_ran = any(
                h.get("expert") == ExpertType.COMPLAINT.value
                for h in expert_history
            )
            if is_p0_escalation(list(metadata["sentiment_hits"])) and not complaint_ran:
                decision = _make_decision(
                    ExpertAction.RUN_EXPERT, ExpertType.COMPLAINT, layer=1,
                    reason=(
                        "P0 投诉信号直通: "
                        f"{','.join(metadata['sentiment_hits'][:3])}"
                    ),
                )
                _record_decision(decision)
                return decision

    # ── Layer 2b: expert 重复检测 ──
    if _is_expert_repeating(expert_history):
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=2,
            reason="expert 重复执行 — 终止防止死循环",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 1c: confidence 降级 ──
    if confidence < CS_CONFIDENCE_CAUTIOUS and not expert_history:
        # P2.1（audit #156）：低置信但意图明确的知识类请求放行检索——
        # 知识库命中自带相关性校验，比泛化兜底回复更可用；
        # 高权限（requires_auth）或非知识类维持拦截（低置信执行动作危险）。
        expert = _resolve_expert(cs_route)
        requires_auth = bool(cs_route.get("requires_auth"))
        risk_level = str(cs_route.get("risk_level", "low"))
        if (
            expert == ExpertType.KNOWLEDGE.value
            and not requires_auth
            and risk_level == "low"
        ):
            decision = _make_decision(
                ExpertAction.RUN_EXPERT, ExpertType.KNOWLEDGE, layer=1,
                reason=(
                    f"低置信度 ({confidence:.2f} < {CS_CONFIDENCE_CAUTIOUS}) "
                    "但意图为知识类（低风险无权限）— 放行知识检索兜底"
                ),
            )
            _record_decision(decision)
            return decision

        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=f"低置信度 ({confidence:.2f} < {CS_CONFIDENCE_CAUTIOUS}) 且无历史 — 降级兜底",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── Layer 3: LLM 决策（仅低置信度时启用）──
    if confidence < CS_CONFIDENCE_CAUTIOUS and expert_history:
        llm_decision = _llm_decision(state)
        if llm_decision is not None:
            _record_decision(llm_decision)
            return llm_decision

    # ── 默认: route_path → expert ──
    expert = _resolve_expert(cs_route)

    # P2.1 跟进：低置信场景下 LLM 决策不可用时，不重复派发刚执行过的同一
    # expert（回答已产出，重跑纯浪费；Layer 2b 重复检测要到第 3 次派发才拦）
    if (
        confidence < CS_CONFIDENCE_CAUTIOUS
        and expert_history
        and expert_history[-1].get("expert") == expert
    ):
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=3,
            reason=(
                f"低置信度 ({confidence:.2f}) 且 LLM 决策不可用 — "
                f"{expert} 刚已执行，直接收尾防止重复"
            ),
            is_finished=True,
        )
        _record_decision(decision)
        return decision
    # P2.1（audit #197）：decision_layer 如实标注——低置信落到这里的唯一情形
    # 是 Layer 3 LLM 决策不可用后的规则降级（此时确无 LLM 参与），注明之；
    # 此前无差别标 layer=3 造成标注失真。
    if confidence >= CS_CONFIDENCE_CAUTIOUS:
        layer, layer_note = 1, ""
    else:
        layer, layer_note = 3, "（LLM 决策不可用，规则降级放行）"
    decision = _make_decision(
        ExpertAction.RUN_EXPERT,
        ExpertType(expert),
        layer=layer,
        reason=f"route → expert={expert} (confidence={confidence:.2f}){layer_note}",
    )
    _record_decision(decision)
    return decision


def _decision_v2(state: dict[str, Any]) -> CSSupervisorDecision:
    """七层固定优先级（迁移 B6，设计方案 §4.2；顺序即语义，禁止重排）。

      1 handoff 状态   2 pending 确认   3 风险状态     4 循环与预算
      5 意图路由       6 低置信处理      7 LLM 兜底

    与 v1 的差异只在多条件并存时的裁决（单条件行为等价）：
    pending/风险先于循环；意图路由（强先验）先于低置信分支。
    decision_layer 标签保持 {1:rule, 2:combination, 3:llm} 度量口径不变，
    优先级层号以 [v2·L*] 前缀写入 reason（trace 可归因）。
    """
    cs_route = state.get("cs_route", {})
    confidence = cs_route.get("confidence", 0.0)
    handoff_state = state.get("handoff_state", "ai_active")
    confirmation_state = state.get("confirmation_state", "not_required")
    expert_loop_count = state.get("expert_loop_count", 0)
    expert_history = state.get("expert_history", [])

    from backend.config.customer_service import (
        CS_CONFIDENCE_CAUTIOUS,
        CS_EXPERT_MAX_LOOPS,
        CS_SIGNAL_GATE_ENABLED,
    )

    # ── 第 1 层：handoff 状态（人工排队/接管中，AI 零抢答）──
    from backend.customer_service.handoff import HandoffState, should_intercept
    try:
        _hs = HandoffState(handoff_state) if handoff_state else HandoffState.AI_ACTIVE
    except ValueError:
        # STOP CS-A P0-6：未知 handoff 状态禁止静默吞成 ai_active ——
        # 数据库侧已有 CHECK 约束（migration 078）挡写入，此处出现未知值
        # 只可能是程序错误/脏数据，fail loud 让本轮显式失败并留痕
        # （cs_graph_node 异常路径会记 logger.exception + trace error）。
        logger.error(
            "[CS Supervisor] unknown handoff_state=%r (user=%s conv=%s) —— fail loud",
            handoff_state, state.get("user_id"), state.get("conversation_id"),
        )
        raise
    if should_intercept(_hs):
        # 排队态分流（C 案）：waiting_human 期间非客服诉求直答，不被
        # 排队话术吞掉；human_active 不进分流（AI 零抢答铁律）。
        if _hs == HandoffState.WAITING_HUMAN:
            divert = _waiting_human_divert(state)
            if divert is not None:
                _record_decision(divert)
                return divert
        decision = _make_decision(
            ExpertAction.HANDOFF, ExpertType.HANDOFF, layer=1,
            reason=f"[v2·L1] handoff 拦截: state={handoff_state}",
            requires_handoff=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── 第 2 层：pending 确认（等用户确认，不被任何分支打断）──
    if confirmation_state in ("pending", "pending_confirmation"):
        decision = _make_decision(
            ExpertAction.PENDING, None, layer=2,
            reason="[v2·L2] confirmation pending — 等待用户确认",
            requires_confirmation=True,
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── 第 3 层：风险状态（understanding 信号兜底，B5 同款两分支）──
    if CS_SIGNAL_GATE_ENABLED:
        metadata = cs_route.get("metadata") or {}
        risk_hits = list(metadata.get("risk_hits") or [])
        if risk_hits:
            decision = _make_decision(
                ExpertAction.FINISH, None, layer=1,
                reason=(
                    f"[v2·L3] 风险信号兜底拦截: {','.join(risk_hits[:3])} — "
                    "拒答+建议转人工"
                ),
                is_finished=True,
            )
            _record_decision(decision)
            try:  # M12：拒答计数（/domain-ops 指标，软失败）
                from backend.observability.metrics import cs_rejection_total
                cs_rejection_total.labels(layer="risk_gate").inc()
            except Exception:
                pass
            return decision
        if metadata.get("sentiment_hits"):
            from backend.customer_service.understanding.signals import (
                is_p0_escalation,
            )

            # 直通仅限本轮 complaint 未执行过——信号是静态的，不设防重入
            # 会压过第 4 层循环守卫形成死循环（B6 实机验证暴露的
            # GraphRecursionError；v1 同位置同缺陷已同修）
            complaint_ran = any(
                h.get("expert") == ExpertType.COMPLAINT.value
                for h in expert_history
            )
            if is_p0_escalation(list(metadata["sentiment_hits"])) and not complaint_ran:
                decision = _make_decision(
                    ExpertAction.RUN_EXPERT, ExpertType.COMPLAINT, layer=1,
                    reason=(
                        "[v2·L3] P0 投诉信号直通: "
                        f"{','.join(metadata['sentiment_hits'][:3])}"
                    ),
                )
                _record_decision(decision)
                return decision

    # ── 第 4 层：循环与预算（防死循环守卫）──
    if expert_loop_count >= CS_EXPERT_MAX_LOOPS:
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=f"[v2·L4] expert 循环达上限 ({CS_EXPERT_MAX_LOOPS})",
            is_finished=True,
        )
        _record_decision(decision)
        return decision
    if _is_expert_repeating(expert_history):
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=2,
            reason="[v2·L4] expert 重复执行 — 终止防止死循环",
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── 第 4.5 层：分诊直出出口（T6，2026-10-04 对话体验改造）────────
    # 位置语义：守卫兜底（handoff/pending/风险/循环）优先级高于对话体验
    # 出口；出口先于 L5 意图路由——寒暄/出域消息 confidence 低，若落到
    # L6/L7 会被知识检索白烧一遍。v1 回退路径（CS_DECISION_V2=false）不接
    # 出口，保持存量语义纯净（回滚=v2 关闭时行为与改造前逐字节一致）。
    direct = _triage_direct_reply(state)
    if direct is not None:
        _record_decision(direct)
        return direct

    # ── 第 5 层：意图路由（route_path 强先验，绝大多数流量到此为止）──
    if confidence >= CS_CONFIDENCE_CAUTIOUS:
        expert = _resolve_expert(cs_route)
        decision = _make_decision(
            ExpertAction.RUN_EXPERT, ExpertType(expert), layer=1,
            reason=f"[v2·L5] route → expert={expert} (confidence={confidence:.2f})",
        )
        _record_decision(decision)
        return decision

    # ── 第 6 层：低置信处理（无专家历史）──
    if not expert_history:
        # P2.1（audit #156）：低置信但意图明确的知识类请求放行检索——
        # 知识库命中自带相关性校验，比泛化兜底回复更可用；
        # 高权限（requires_auth）或非知识类维持拦截（低置信执行动作危险）。
        expert = _resolve_expert(cs_route)
        requires_auth = bool(cs_route.get("requires_auth"))
        risk_level = str(cs_route.get("risk_level", "low"))
        if (
            expert == ExpertType.KNOWLEDGE.value
            and not requires_auth
            and risk_level == "low"
        ):
            decision = _make_decision(
                ExpertAction.RUN_EXPERT, ExpertType.KNOWLEDGE, layer=1,
                reason=(
                    f"[v2·L6] 低置信度 ({confidence:.2f} < {CS_CONFIDENCE_CAUTIOUS}) "
                    "但意图为知识类（低风险无权限）— 放行知识检索兜底"
                ),
            )
            _record_decision(decision)
            return decision

        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=(
                f"[v2·L6] 低置信度 ({confidence:.2f} < {CS_CONFIDENCE_CAUTIOUS}) "
                "且无历史 — 降级兜底"
            ),
            is_finished=True,
        )
        _record_decision(decision)
        return decision

    # ── 第 7 层：LLM 兜底（低置信 + 有专家历史，CS_SUPERVISOR_LLM_ENABLED 可关）──
    llm_decision = _llm_decision(state)
    if llm_decision is not None:
        _record_decision(llm_decision)
        return llm_decision

    # 确定性回退：不重复派发刚执行过的同一 expert（回答已产出，重跑纯浪费）
    expert = _resolve_expert(cs_route)
    if expert_history and expert_history[-1].get("expert") == expert:
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=3,
            reason=(
                f"[v2·L7] 低置信度 ({confidence:.2f}) 且 LLM 决策不可用 — "
                f"{expert} 刚已执行，直接收尾防止重复"
            ),
            is_finished=True,
        )
        _record_decision(decision)
        return decision
    decision = _make_decision(
        ExpertAction.RUN_EXPERT,
        ExpertType(expert),
        layer=3,
        reason=(
            f"[v2·L7] 低置信度 ({confidence:.2f}) LLM 决策不可用 — "
            f"规则降级 route → expert={expert}"
        ),
    )
    _record_decision(decision)
    return decision


def _triage_direct_reply(state: dict[str, Any]) -> CSSupervisorDecision | None:
    """分诊直出出口（T6，2026-10-04 对话体验改造任务卡）。

    两个出口（词表口径见 vocab.py「出域与寒暄词表」段）：
      - 寒暄（CHITCHAT_PATTERNS）→ chat_fallback 一次 LLM 人设生成
        （CS_CHAT_FALLBACK_ENABLED 控制，关闭时 run_chat_fallback 返 None
        落回旧漏斗路径）；
      - 出域（OUT_OF_SCOPE_PATTERNS）→ 固定话术，零 LLM 零检索
        （CS_WINDOW_STANDALONE 控制——与 router T5 反转同一开关：
        standalone=false 时出域话题已在 router 层转出主路由，域内出口
        自然不触发，同开关避免两处语义漂移）。

    顺序铁律（T4）：先客服信号后出域/寒暄——消息带客服域规则信号
    （如"订单里的行程单丢了"）不得被出域词表截胡，落业务漏斗；与
    router redirect 阶段一同源口径（cs_rule_hit_count 非空即留守）。
    吃不准（信号判定失败）保守落业务漏斗：宁可尝试不可错拒。

    返回 None = 出口未命中 / 对应开关关闭 / 信号判定失败，调用方继续
    原决策链。词表判定纯规则零 LLM；寒暄出口的 LLM 调用在
    chat_fallback 内部（带线程级限时 + PII 脱敏 + fail-open 固定话术）。
    """
    from backend.config.customer_service import CS_WINDOW_STANDALONE
    from backend.customer_service.chat_fallback import (
        chat_fallback_enabled,
        run_chat_fallback,
    )
    from backend.customer_service.vocab import (
        format_out_of_scope,
        match_chitchat,
        match_out_of_scope,
    )

    # CS 图键 = user_message（graph_state 权威）；question/query 为主图
    # 键，兼容直测与跨图复用场景。
    query_text = (
        state.get("user_message")
        or state.get("question")
        or state.get("query")
        or ""
    ).strip()
    if not query_text:
        return None

    # 顺序铁律（精确口径 2026-10-04.2）：业务域词命中 → 不判出域/寒暄
    # （纯正则 ~µs 级）。不用全域规则命中数——KNOWLEDGE 通用疑问词
    # （怎么/如何）出现在几乎一切疑问句里，会让出域出口失效；业务词
    # （订单/退款/物流…）才构成真实客服诉求的豁免信号。
    try:
        from backend.customer_service.vocab import match_cs_signal_exempt
        if match_cs_signal_exempt(query_text):
            return None
    except Exception:
        return None

    # 出口一：寒暄 → 一次 LLM 人设生成（词表段口径：先寒暄后出域——
    # 纯"你好"不该收到出域话术）
    if chat_fallback_enabled() and match_chitchat(query_text):
        result = run_chat_fallback(query_text)
        if result is not None:
            decision = _make_decision(
                ExpertAction.FINISH, None, layer=1,
                reason=(
                    "[v2·L4.5] 寒暄分诊直出: chat_fallback 一次 LLM "
                    f"(pii_masked={result.pii_masked}"
                    + (", llm_error_fallback" if result.error else "") + ")"
                ),
                is_finished=True,
            )
            decision["direct_reply"] = result.reply
            _tag_triage_direct("chitchat", bool(result.error))
            return decision

    # 出口二：出域 → 引导（分诊阶梯 A 案）或固定话术（V1 验收口径：零 LLM）
    if CS_WINDOW_STANDALONE and match_out_of_scope(query_text):
        topic = _out_of_scope_topic(query_text)
        guide = _out_of_scope_guide_reply(query_text, topic)
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=(
                f"[v2·L4.5] 出域{'引导:' + guide[0] if guide else '固定话术'}: "
                f"{topic}（零 LLM 零检索）"
            ),
            is_finished=True,
        )
        decision["direct_reply"] = guide[1] if guide else format_out_of_scope(topic)
        _tag_triage_direct("out_of_scope_guide" if guide else "out_of_scope", False)
        if not guide:  # M12 口径：固定话术属拒答类；引导是服务出口不计数
            try:
                from backend.observability.metrics import cs_rejection_total
                cs_rejection_total.labels(layer="out_of_scope").inc()
            except Exception:
                pass
        return decision

    return None


def _out_of_scope_guide_reply(query_text: str, topic: str) -> tuple[str, str] | None:
    """出域引导（A 案，CS_TRIAGE_GUIDE_ENABLED）：旅游/选品话题指路对口域。

    返回 (去向, 引导话术)；未开启开关 / 平台外话题返回 None（落固定话术）。
    引导命中组 ⊆ 出域词表，顺序铁律已由调用方保证。
    """
    from backend.config.customer_service import CS_TRIAGE_GUIDE_ENABLED
    from backend.customer_service.vocab import (
        format_out_of_scope_guide,
        match_out_of_scope_guide,
    )
    if not CS_TRIAGE_GUIDE_ENABLED:
        return None
    guide = match_out_of_scope_guide(query_text)
    if not guide:
        return None
    return guide, format_out_of_scope_guide(guide, topic)


def _waiting_human_divert(state: dict[str, Any]) -> CSSupervisorDecision | None:
    """排队态消息分流（C 案，CS_WAITING_HUMAN_DIVERT_ENABLED）。

    waiting_human 期间的非客服诉求不再被排队话术吞掉：
      - 寒暄 → chat_fallback 直答；出域 → 固定话术/引导直答；
      - 客服诉求（催单/订单/退款等业务域词）→ None，保持排队话术
        （该话术本身承载「已同步人工」语义）；
      - human_active（人工已接入）不进来——AI 零抢答铁律不动。
    全程复用既有词表与出口实现，零新词表、零新 LLM 面。
    """
    from backend.config.customer_service import CS_WAITING_HUMAN_DIVERT_ENABLED
    from backend.customer_service.handoff import HandoffState
    if not CS_WAITING_HUMAN_DIVERT_ENABLED:
        return None
    if state.get("handoff_state") != HandoffState.WAITING_HUMAN.value:
        return None

    query_text = (
        state.get("user_message")
        or state.get("question")
        or state.get("query")
        or ""
    ).strip()
    if not query_text:
        return None
    # 顺序铁律同源：客服域词命中 = 催单/补充类诉求，保持排队话术
    try:
        from backend.customer_service.vocab import match_cs_signal_exempt
        if match_cs_signal_exempt(query_text):
            return None
    except Exception:
        return None

    from backend.customer_service.chat_fallback import (
        chat_fallback_enabled,
        run_chat_fallback,
    )
    from backend.customer_service.vocab import (
        format_out_of_scope,
        match_chitchat,
        match_out_of_scope,
    )

    if chat_fallback_enabled() and match_chitchat(query_text):
        result = run_chat_fallback(query_text)
        if result is not None:
            decision = _make_decision(
                ExpertAction.FINISH, None, layer=1,
                reason=(
                    "[v2·L1-divert] 排队期寒暄直答: chat_fallback"
                    + ("(llm_error_fallback)" if result.error else "")
                ),
                is_finished=True,
            )
            decision["direct_reply"] = result.reply
            _tag_triage_direct("waiting_chitchat", bool(result.error))
            return decision

    if match_out_of_scope(query_text):
        topic = _out_of_scope_topic(query_text)
        guide = _out_of_scope_guide_reply(query_text, topic)
        decision = _make_decision(
            ExpertAction.FINISH, None, layer=1,
            reason=(
                "[v2·L1-divert] 排队期出域直答: "
                + ("引导:" + guide[0] if guide else f"固定话术 {topic}")
            ),
            is_finished=True,
        )
        decision["direct_reply"] = guide[1] if guide else format_out_of_scope(topic)
        _tag_triage_direct("waiting_out_of_scope", False)
        return decision

    return None


def _out_of_scope_topic(query_text: str) -> str:
    """出域话术 topic：取第一个命中词表模式的片段（旅游/选品/平台外）。"""
    from backend.customer_service.vocab import OUT_OF_SCOPE_PATTERNS
    for pattern in OUT_OF_SCOPE_PATTERNS:
        matched = pattern.search(query_text)
        if matched:
            return matched.group(0)
    return "该问题"


def _tag_triage_direct(kind: str, degraded: bool) -> None:
    """出口 trace 打点（任务卡 T6：出域/寒暄出口可归因），软失败。"""
    try:
        from backend.observability.tracer import trace_collector
        tracer = trace_collector.current()
        if tracer is not None:
            tracer.tags["cs_triage_direct"] = kind
            if degraded:
                tracer.tags["cs_triage_direct_degraded"] = "llm_fallback"
    except Exception:
        pass


def _is_expert_repeating(expert_history: list[dict]) -> bool:
    """检测同一 expert 是否连续执行 2 次。"""
    if len(expert_history) < 2:
        return False
    return (
        expert_history[-1].get("expert") == expert_history[-2].get("expert")
    )


def _llm_decision(state: dict[str, Any]) -> CSSupervisorDecision | None:
    """Layer 3: LLM 决策（带超时 + 确定性降级）。

    仅在 CS_SUPERVISOR_LLM_ENABLED=true 时启用。
    超时或异常时返回 None，调用方回退到默认路由。
    """
    from backend.config.customer_service import (
        CS_SUPERVISOR_LLM_ENABLED,
        CS_SUPERVISOR_LLM_TIMEOUT_MS,
    )

    if not CS_SUPERVISOR_LLM_ENABLED:
        return None

    cs_route = state.get("cs_route", {})
    user_message = state.get("user_message", "")
    expert_history = state.get("expert_history", [])

    prev_experts = [e.get("expert", "") for e in expert_history]
    # E4b（2026-10-05）：LLM payload 零明文 PII——决策只需意图语义，
    # 掩码后不还原（还原会回注明文进回复链）。
    from backend.customer_service.pii import mask_pii
    masked_message, _vault = mask_pii(user_message)
    prompt = render_prompt(
        "customer_service.supervisor",
        user_message=masked_message[:200],
        intent=cs_route.get("intent", "unknown"),
        expert_history=str(prev_experts),
    )

    try:
        from langchain_core.messages import HumanMessage

        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm import llm  # 代理：限流/韧性/llm_usage 记账

        t0 = time.monotonic()
        timeout_s = CS_SUPERVISOR_LLM_TIMEOUT_MS / 1000.0

        # config={"timeout"} 实测不生效（2026-09），须线程级限时
        response = sync_call_with_timeout(
            llm.invoke, timeout_s, [HumanMessage(content=prompt)],
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        chosen = response.content.strip().lower()
        valid_experts = {e.value for e in ExpertType}
        if chosen not in valid_experts:
            logger.warning(
                "[CS Supervisor L3] LLM 返回无效 expert=%s, 降级", chosen,
            )
            return None

        logger.info(
            "[CS Supervisor L3] LLM chose expert=%s elapsed_ms=%d",
            chosen, elapsed_ms,
        )

        return _make_decision(
            ExpertAction.RUN_EXPERT,
            ExpertType(chosen),
            layer=3,
            reason=f"LLM 决策: expert={chosen} ({elapsed_ms}ms)",
        )

    except Exception as e:
        logger.warning("[CS Supervisor L3] LLM 调用失败，降级到默认路由: %s", e)
        return None


def _record_decision(decision: CSSupervisorDecision) -> None:
    """埋点 Supervisor 决策。"""
    try:
        from backend.observability.metrics import record_cs_supervisor_decision
        layer_label = {1: "rule", 2: "combination", 3: "llm"}.get(
            decision["decision_layer"], "unknown"
        )
        record_cs_supervisor_decision(layer_label, decision["next_action"])
        # cs_supervisor_decision_source（2026-10-08 LLM 收口）：trace 归因
        # rule/llm/fallback——L7 规则降级（LLM 不可用/白名单无效）必须与
        # LLM 真实决策可区分（P0-23）。
        from backend.observability.tracer import trace_collector
        tracer = trace_collector.current()
        if tracer is not None:
            if decision["decision_layer"] == 3:
                source = (
                    "fallback" if "LLM 决策不可用" in decision.get("reason", "")
                    else "llm"
                )
            else:
                source = "rule"
            tracer.tags["cs_supervisor_decision_source"] = source
    except Exception:
        pass


def _recover_handoff_timeout(state: dict[str, Any]) -> dict[str, Any] | None:
    """人工接入超时回退（CS_HANDOFF_TIMEOUT_SECONDS）。

    handoff_requested / waiting_human 超过时限仍无人工接入 → 关闭本次
    转接（状态机两者均可合法转换到 CLOSED），清 store，handoff_state
    归 ai_active，本 turn 恢复正常专家路由。未超时/无活跃转接返回 None。
    """
    from datetime import datetime, timezone

    from backend.config.customer_service import CS_HANDOFF_TIMEOUT_SECONDS
    from backend.customer_service.audit import append_audit, build_audit_entry
    from backend.customer_service.handoff_store import get_handoff_store

    handoff_state = state.get("handoff_state", "ai_active")
    if handoff_state not in ("handoff_requested", "waiting_human"):
        return None

    user_id = state.get("user_id", "")
    session_id = state.get("session_id", "default")
    store = get_handoff_store()
    active = store.get_active_handoff(user_id)
    if not active:
        return None

    stamp = active.get("updated_at") or active.get("created_at")
    if not stamp:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    if age.total_seconds() < CS_HANDOFF_TIMEOUT_SECONDS:
        return None

    store.clear(user_id, session_id)
    logger.warning(
        "[CS Supervisor] 人工接入超时 (%.0fs >= %ds)，回退 AI 服务: user=%s",
        age.total_seconds(), CS_HANDOFF_TIMEOUT_SECONDS, user_id,
    )
    audit_entry = build_audit_entry(
        user_id=user_id or "anonymous",
        action_type="handoff_timeout_recovered",
        result="success",
        target_type="handoff",
        target_id=str(active.get("ticket_id", "")),
        detail=f"handoff timeout after {int(age.total_seconds())}s, recovered to ai_active",
        conversation_id=state.get("conversation_id", ""),
    )
    return {
        "handoff_state": "ai_active",
        "cs_audit_entries": append_audit(
            list(state.get("cs_audit_entries", [])), audit_entry,
        ),
        "cs_context": {
            **(state.get("cs_context") or {}),
            "handoff_state": "ai_active",
        },
    }


def cs_supervisor_node(state: dict[str, Any]) -> Command:
    """CS Supervisor 节点函数 — 返回 Command(goto=..., update={...})

    Phase 4: 使用 Command 模式替代 conditional edges。
    """
    from backend.customer_service.graph_state import (
        CS_ACTION_EXPERT,
        CS_COMPLAINT_EXPERT,
        CS_HANDOFF_EXPERT,
        CS_KNOWLEDGE_EXPERT,
        CS_QUERY_EXPERT,
        CS_REPORTER,
    )

    # STOP CS-A P0-3（F2）：Supervisor 入口取消检查 —— 不再派发任何新专家
    from backend.core.request_context import raise_if_cancelled

    raise_if_cancelled("cs_supervisor")

    timeout_update = _recover_handoff_timeout(state)
    if timeout_update is not None:
        state = {**state, **timeout_update}

    decision = make_supervisor_decision(state)
    action = decision["next_action"]
    expert = decision.get("next_expert", "")

    if action in (ExpertAction.FINISH.value, ExpertAction.PENDING.value):
        target = CS_REPORTER
    elif action == ExpertAction.HANDOFF.value and decision.get("is_finished"):
        target = CS_REPORTER
    elif action == ExpertAction.HANDOFF.value:
        target = CS_HANDOFF_EXPERT
    else:
        # P2.2：expert 语义名 → 节点名改查 graph_state 权威表
        target = _EXPERT_NAME_TO_NODE.get(expert, CS_KNOWLEDGE_EXPERT)

    logger.info(
        "[CS Supervisor] action=%s expert=%s layer=%d reason=%s → %s",
        action, expert, decision["decision_layer"], decision["reason"], target,
    )

    return Command(
        goto=target,
        update={
            "supervisor_decision": dict(decision),
            "current_expert": expert or "",
            "expert_loop_count": state.get("expert_loop_count", 0) + 1,
            **(timeout_update or {}),
        },
    )
