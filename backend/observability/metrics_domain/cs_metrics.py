"""客服、工单完整性、Outbox 队列与派单指标。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# 旅游域 Failure Policy 结局指标（2026-10-07 Tool 失败降级改造）：调用/失败/
# 重试/熔断由 agent_tool_* 族按 domain=travel 覆盖，这里只补工作流结局维度。
# 标签全部受控词表（tool 名 / provider 常量），禁 conversation_id 等高基数。
travel_tool_degraded_total = Counter(
    "travel_tool_degraded_total",
    "旅游域 Tool 降级次数（外部数据源失败但业务继续，行程已生成）",
    labelnames=("tool", "provider"),
)


travel_tool_blocked_total = Counter(
    "travel_tool_blocked_total",
    "旅游域硬依赖阻断次数（依赖无法验证，Workflow 终止）",
    labelnames=("tool", "provider"),
)


# ── 客服系统指标（Phase 6）──
cs_intent_total = Counter(
    "cs_intent_total",
    "客服意图分类总数（按意图类型）",
    labelnames=("intent",),
)


cs_permission_violation_total = Counter(
    "cs_permission_violation_total",
    "客服权限校验违规总数",
    labelnames=("action",),
)


cs_action_total = Counter(
    "cs_action_total",
    "客服业务操作总数（按操作类型与结果）",
    labelnames=("action", "result"),
)


cs_confirmation_total = Counter(
    "cs_confirmation_total",
    "客服确认状态转换总数",
    labelnames=("transition",),
)


cs_ticket_integrity_total = Counter(
    "cs_ticket_integrity_total",
    "工单完整性降级总数（投诉幂等预检跳过/投诉与转接的工单镜像落库失败"
    "——不阻断主流程的声明式取舍，但必须可被运维察觉而非只有 warning 日志）",
    labelnames=("kind",),
)


cs_handoff_total = Counter(
    "cs_handoff_total",
    "客服人工转接总数（按触发类型）",
    labelnames=("trigger",),
)


cs_rag_status_total = Counter(
    "cs_rag_status_total",
    "客服 RAG 查询状态总数",
    labelnames=("status",),
)


cs_rejection_total = Counter(
    "cs_rejection_total",
    "客服域拒答收尾总数（风险兜底拦截等，M12 /domain-ops 指标）",
    labelnames=("layer",),
)


travel_slot_clarify_total = Counter(
    "travel_slot_clarify_total",
    "旅游域 slot_filler 追问次数（缺槽触发 build_clarification，M12 指标）",
    labelnames=("missing_count_bucket",),
)


# ── 旅游域 LLM 理解层（2026-10-08 STOP 1/4：Slot Fallback + Clarification Renderer）──
# status 取值域：ok（候选≥1）/ empty（合法但零候选）/ schema_invalid /
# error / disabled。fallback 记「本次未采用 LLM 结果」的原因。
travel_slot_llm_total = Counter(
    "travel_slot_llm_total",
    "旅游域槽位 LLM 富化调用数",
    labelnames=("status",),
)


travel_slot_llm_fallback_total = Counter(
    "travel_slot_llm_fallback_total",
    "旅游域槽位 LLM 富化回落规则数（reason=disabled|no_model|llm_error|"
    "invalid_json|schema_invalid|low_confidence|no_candidates）",
    labelnames=("reason",),
)


travel_slot_llm_latency_seconds = Histogram(
    "travel_slot_llm_latency_seconds",
    "旅游域槽位 LLM 富化耗时",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0),
)


# source 取值域：llm / template。fallback 记 LLM 渲染未采用的原因。
travel_clarification_total = Counter(
    "travel_clarification_total",
    "旅游域追问渲染数（按最终采用来源计）",
    labelnames=("source",),
)


travel_clarification_fallback_total = Counter(
    "travel_clarification_fallback_total",
    "旅游域追问 LLM 渲染回落模板数（reason=disabled|no_model|llm_error|"
    "invalid_json|empty|too_long|forbidden_content）",
    labelnames=("reason",),
)


travel_clarification_latency_seconds = Histogram(
    "travel_clarification_latency_seconds",
    "旅游域追问渲染耗时",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0),
)


cs_store_db_failure_total = Counter(
    "cs_store_db_failure_total",
    "客服状态 Store DB 写失败总数（strict 模式抛错，非 strict 告警降级）",
    labelnames=("store", "op"),
)


cs_knowledge_meta_fallback_total = Counter(
    "cs_knowledge_meta_fallback_total",
    "知识问答 META 缺失兜底计数（B9 收紧为 REFUSE；兜底率上升 = 模型 "
    "META 注释遵循度劣化信号，设计方案 §4.3 兜底值监控）",
)


# ── CS Graph 独立架构指标（Phase 0 新增）──
cs_supervisor_decision_total = Counter(
    "cs_supervisor_decision_total",
    "CS Supervisor 决策总数（按决策层与动作类型）",
    labelnames=("layer", "action"),
)


cs_expert_result_total = Counter(
    "cs_expert_result_total",
    "CS Expert 执行结果总数（按专家类型与状态）",
    labelnames=("expert", "status"),
)


# ── CS LLM 语义层/回复层指标（2026-10-08 LLM 收口改造）──
# 任务书 §二十九：label 只用低基数枚举，禁止 user_id/order_id/手机号/原文。
cs_understanding_llm_total = Counter(
    "cs_understanding_llm_total",
    "CS 意图理解 LLM 调用结果（status=accepted|no_candidate|rule_skipped|"
    "disabled——rule_skipped 即 Rule First 达成计数）",
    labelnames=("status",),
)


cs_slot_llm_total = Counter(
    "cs_slot_llm_total",
    "CS 语义槽位 LLM 调用结果（status=accepted|rejected|no_candidate）",
    labelnames=("status",),
)


cs_slot_llm_fallback_total = Counter(
    "cs_slot_llm_fallback_total",
    "CS 语义槽位 LLM 回落数（reason=llm_error|invalid_json|schema_fail|"
    "empty|prompt_unavailable|empty_input）",
    labelnames=("reason",),
)


cs_response_total = Counter(
    "cs_response_total",
    "CS 回复生成来源（source=template|llm|llm_fallback；knowledge 的 "
    "llm=RAG 上游生成直通）",
    labelnames=("source",),
)


cs_response_llm_fallback_total = Counter(
    "cs_response_llm_fallback_total",
    "CS 回复 LLM 转写回落数（reason=llm_error|guard_fail）",
    labelnames=("reason",),
)


def record_cs_intent(intent: str) -> None:
    """埋点客服意图分类。"""
    try:
        cs_intent_total.labels(intent=intent).inc()
    except Exception:
        pass


def record_cs_permission_violation(action: str) -> None:
    """埋点客服权限违规。"""
    try:
        cs_permission_violation_total.labels(action=action).inc()
    except Exception:
        pass


def record_cs_action(action: str, result: str) -> None:
    """埋点客服业务操作（action: refund/return/exchange, result: success/failed/rejected）。"""
    try:
        cs_action_total.labels(action=action, result=result).inc()
    except Exception:
        pass


def record_cs_confirmation(transition: str) -> None:
    """埋点客服确认状态转换（initiated/confirmed/cancelled/expired）。"""
    try:
        cs_confirmation_total.labels(transition=transition).inc()
    except Exception:
        pass


def record_cs_handoff(trigger: str) -> None:
    """埋点客服人工转接（explicit/low_confidence/consecutive_fail/complaint）。"""
    try:
        cs_handoff_total.labels(trigger=trigger).inc()
    except Exception:
        pass


def record_cs_rag_status(status: str) -> None:
    """埋点客服 RAG 查询状态（hit/miss/rejected）。"""
    try:
        cs_rag_status_total.labels(status=status).inc()
    except Exception:
        pass


def record_cs_knowledge_meta_fallback() -> None:
    """埋点知识问答 META 缺失兜底（B9：兜底即 REFUSE，监控兜底率）。"""
    try:
        cs_knowledge_meta_fallback_total.inc()
    except Exception:
        pass


def record_cs_supervisor_decision(layer: str, action: str) -> None:
    """埋点 CS Supervisor 决策（layer: rule/combination/llm, action: run_expert/finish/handoff/pending）。"""
    try:
        cs_supervisor_decision_total.labels(layer=layer, action=action).inc()
    except Exception:
        pass


def record_cs_expert_result(expert: str, status: str) -> None:
    """埋点 CS Expert 执行结果（expert: knowledge/query/action/complaint/handoff, status: success/failed/timeout）。"""
    try:
        cs_expert_result_total.labels(expert=expert, status=status).inc()
    except Exception:
        pass


def record_cs_store_db_failure(store: str, op: str) -> None:
    """埋点客服状态 Store DB 写失败（store: confirmation/handoff, op: save/clear/claim）。"""
    try:
        cs_store_db_failure_total.labels(store=store, op=op).inc()
    except Exception:
        pass


cs_dispatch_queue_depth = Gauge(
    "cs_dispatch_queue_depth",
    "当前排队中的转人工工单数（waiting_human + agent_offered）",
)


cs_dispatch_offered_total = Counter(
    "cs_dispatch_offered_total",
    "自动派单结果计数（dispatched/no_candidate/presence_unavailable/contended）",
    ["result"],
)


cs_dispatch_wait_seconds = Histogram(
    "cs_dispatch_wait_seconds",
    "工单从入池到被派出的等待时长（含重派前的全部排队时间）",
    buckets=(1, 2, 5, 10, 30, 60, 120, 300, 600),
)


cs_dispatch_reaped_total = Counter(
    "cs_dispatch_reaped_total",
    "reaper 处理计数（released=回队列重派 / closed 原因=max_attempts|total_deadline）",
    ["action"],
)


cs_dispatch_online_agents = Gauge(
    "cs_dispatch_online_agents",
    "当前启用且可接单的坐席数（用于在线坐席掉底告警）",
)


cs_outbox_pending = Gauge(
    "cs_outbox_pending",
    "outbox 中尚未成功投递的事件数（持续增长 = relay 故障）",
)


cs_outbox_lag_seconds = Gauge(
    "cs_outbox_lag_seconds",
    "最旧 pending 事件距现在的秒数（方案 P8 完成标准：P99 < 2 秒）",
)


cs_outbox_published_total = Counter(
    "cs_outbox_published_total",
    "outbox 事件投递计数（published / deferred）",
    ["result"],
)


def record_cs_dispatch_result(result: str) -> None:
    """埋点一次派单尝试的终态。"""
    try:
        cs_dispatch_offered_total.labels(result=result).inc()
    except Exception:
        pass


def record_cs_dispatch_wait(seconds: float) -> None:
    """埋点工单等待时长（入池 → 派出）。"""
    try:
        cs_dispatch_wait_seconds.observe(max(0.0, seconds))
    except Exception:
        pass


def record_cs_reaped(action: str) -> None:
    """埋点 reaper 动作：released / closed_max_attempts / closed_total_deadline。"""
    try:
        cs_dispatch_reaped_total.labels(action=action).inc()
    except Exception:
        pass


def set_cs_queue_depth(depth: int) -> None:
    try:
        cs_dispatch_queue_depth.set(max(0, depth))
    except Exception:
        pass


def set_cs_online_agents(count: int) -> None:
    try:
        cs_dispatch_online_agents.set(max(0, count))
    except Exception:
        pass


def set_cs_outbox_pending(count: int) -> None:
    try:
        cs_outbox_pending.set(max(0, count))
    except Exception:
        pass


def set_cs_outbox_lag(seconds: float) -> None:
    try:
        cs_outbox_lag_seconds.set(max(0.0, seconds))
    except Exception:
        pass


def record_cs_outbox_publish(result: str) -> None:
    try:
        cs_outbox_published_total.labels(result=result).inc()
    except Exception:
        pass


cs_qa_daily_conversations = Gauge(
    "cs_qa_daily_conversations",
    "客服质检日报：昨日会话量",
)


cs_qa_daily_handoff_rate = Gauge(
    "cs_qa_daily_handoff_rate",
    "客服质检日报：昨日转人工率",
)


cs_qa_daily_satisfaction = Gauge(
    "cs_qa_daily_satisfaction",
    "客服质检日报：昨日满意度均分（无评价时不上报）",
)


def record_cs_qa_conversations(total: int, handoff_rate: float) -> None:
    try:
        cs_qa_daily_conversations.set(max(0, int(total)))
        cs_qa_daily_handoff_rate.set(max(0.0, float(handoff_rate)))
    except Exception:
        pass


def record_cs_qa_satisfaction(avg_rating) -> None:
    try:
        if avg_rating is not None:
            cs_qa_daily_satisfaction.set(float(avg_rating))
    except Exception:
        pass


