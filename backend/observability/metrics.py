"""Prometheus 指标 + /metrics 端点 — PR-0.3。

4 个核心 SLI（对齐 docs/observability/slo.md 的 SLO 目标）：
- chat_request_total{status}        Counter
- chat_request_duration_seconds     Histogram
- llm_tokens_total{model, direction} Counter
- skill_failure_total{skill, error_type} Counter

业务模块调用方式：
    from backend.observability.metrics import (
        chat_request_total, chat_request_duration_seconds,
        llm_tokens_total, skill_failure_total,
    )
    chat_request_total.labels(status="ok").inc()

HTTP 端点 /metrics 在 server.py 注册。

4 个运营指标（2026-08-11 PRD+TRD 完善计划）：
- rag_hit_rate           Gauge — RAG 命中率
- rag_reject_rate        Gauge — RAG 拒答率（Evidence Gate 拦截）
- doc_metadata_coverage  Gauge — 活跃文档 metadata 完整度
- feedback_positive_rate Gauge — 用户反馈 👍 比例
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# ==========================================================
# 4 个核心 metric（PR-0.3 最小骨架；后续可加 workflow_run_duration 等）
# ==========================================================

chat_request_total = Counter(
    "chat_request_total",
    "Total chat API requests by final status",
    labelnames=("status",),  # ok | error | rejected | aborted
)

chat_request_duration_seconds = Histogram(
    "chat_request_duration_seconds",
    "Chat request wall-clock duration in seconds",
    buckets=(0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM token usage by model and direction",
    labelnames=("model", "direction"),  # direction: prompt | completion
)

# =====================================================
# Unified Token Usage Metric (P0 - Backward Compatible)
# =====================================================
# 用于 Embedding / Rerank 的统一 token 统计
# 不修改 llm_tokens_total 的 label 结构，保持向后兼容

token_usage_total = Counter(
    "token_usage_total",
    "Unified token usage by component, model and direction",
    labelnames=("component", "model", "direction"),  # component: embedding|rerank|llm
)

skill_failure_total = Counter(
    "skill_failure_total",
    "Skill execution failures by skill name and error type",
    labelnames=("skill", "error_type"),
)

# ── 流式事件可观测性（P0-1：SSE 队列 backpressure 丢弃计数）──
chat_stream_event_dropped_total = Counter(
    "chat_stream_event_dropped_total",
    "SSE 流式事件被 backpressure / 异常 丢弃的次数",
    labelnames=("reason",),  # queue_full | producer_error
)

chat_stream_event_produced_total = Counter(
    "chat_stream_event_produced_total",
    "SSE 流式事件由 LangGraph 产出的总数（按事件类型）",
    labelnames=("event",),  # status | delta | log | done | error | meta
)

# ── 索引一致性 Sweeper（2026-09-17 P0-1 告警闭环）──
# 五路存储对账检出的问题数：error 级持续增长 = 有数据不一致未修复，需告警
rag_consistency_issues_total = Counter(
    "rag_consistency_issues_total",
    "索引五路存储一致性检查检出的问题数（按存储与严重级别）",
    labelnames=("store", "severity"),  # store: registry|chroma_chunk|chroma_doc|chunk_store|bm25; severity: error|warning
)

rag_consistency_repairs_total = Counter(
    "rag_consistency_repairs_total",
    "Sweeper 执行的修复动作数（按存储与结果）",
    labelnames=("store", "result"),  # result: ok | failed
)

# ── TTFT / TPOT 可观测性（P1 真 token 级流式 + 2026-09-13 补 TPOT）──
# TTFT：首 delta 事件距请求开始的秒数：真流式下 ≈ 首个生成 chunk 到达时间，
# 假打字机回退时 ≈ 全链路耗时。对比两条曲线即可验证流式改造收益。
chat_ttft_seconds = Histogram(
    "chat_ttft_seconds",
    "Time to first delta event (TTFT) in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 60.0),
)

# TPOT：首 delta 到末 delta 之间平均每 token 生成耗时（受 LLM 服务端 decode 速度
# 与网络吞吐影响，与 TTFT 的 prefill 阶段正交）。deltas < 2 时不采样。
chat_tpot_seconds = Histogram(
    "chat_tpot_seconds",
    "Time per output token (TPOT) in seconds between first and last delta",
    buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)


class StreamLatencyTracker:
    """SSE 流式延迟追踪器：记录 TTFT / delta 数 / TPOT，收尾时统一上报。

    TPOT 定义：(末 delta 时间 - 首 delta 时间) / (delta 数 - 1)，
    即 decode 阶段平均每 token 耗时（不含 prefill 的 TTFT 部分）。

    用法:
        tracker = StreamLatencyTracker(start=time.monotonic())
        for evt in events:
            if evt["event"] == "delta":
                tracker.on_delta(time.monotonic())
        tracker.finish()   # finally 中调用，幂等
    """

    def __init__(self, start: float) -> None:
        self._start = start
        self._first_delta: float | None = None
        self._last_delta: float | None = None
        self._delta_count = 0
        self._finished = False

    @property
    def delta_count(self) -> int:
        return self._delta_count

    def on_delta(self, now: float) -> None:
        """每收到一个 delta 事件调用一次。首个 delta 同时记 TTFT。"""
        self._delta_count += 1
        if self._first_delta is None:
            self._first_delta = now
            try:
                chat_ttft_seconds.observe(now - self._start)
            except Exception:
                pass
        self._last_delta = now

    def finish(self) -> None:
        """流结束时计算并上报 TPOT；delta 不足 2 个（无 decode 过程）跳过。幂等。"""
        if self._finished:
            return
        self._finished = True
        if self._first_delta is None or self._delta_count < 2:
            return
        try:
            decode_elapsed = self._last_delta - self._first_delta
            chat_tpot_seconds.observe(decode_elapsed / (self._delta_count - 1))
        except Exception:
            pass

# ── 并发控制可观测性（优先级排队中间件，2026-09-13）──
request_concurrency_active = Gauge(
    "request_concurrency_active",
    "当前占用并发槽位的重量端点请求数",
)
request_concurrency_queued = Gauge(
    "request_concurrency_queued",
    "并发槽位满时正在等待队列中的请求数",
)
request_concurrency_wait_seconds = Histogram(
    "request_concurrency_wait_seconds",
    "重量端点请求获得并发槽位前的排队耗时（按优先级）",
    labelnames=("priority",),  # high | normal
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)
request_concurrency_reject_total = Counter(
    "request_concurrency_reject_total",
    "并发排队超时被拒（503）的请求数（按原因）",
    labelnames=("reason",),  # queue_timeout
)

# ── 运营指标（2026-08-11 新增）──
# 累计计数（用于计算 rates）
rag_query_total = Counter(
    "rag_query_total",
    "RAG 查询总数（按结果状态）",
    labelnames=("status",),  # hit | rejected | fallback
)

# RAG 防线异常软降级计数（R3 fail-visible）：fail-open 语义保留，
# 但每次异常放行必须可见。layer: pre_wrap | gate1 | gate1_deserialize |
# gate1_entity | risk_level | gate2 | claim_verify | faithfulness | prompt
rag_gate_degraded_total = Counter(
    "rag_gate_degraded_total",
    "RAG 防线异常软降级总数（按防线层）",
    labelnames=("layer",),
)

# RAG 生成前短路计数（区分空检索与 Gate 前置拒答，此前两者 trace 不可区分）
rag_short_circuit_total = Counter(
    "rag_short_circuit_total",
    "RAG 生成前短路总数（按原因）",
    labelnames=("reason",),  # empty_retrieval | evidence_gate
)

rag_permission_filtered_total = Counter(
    "rag_permission_filtered_total",
    "RAG 生成上下文中被文档级权限过滤剔除的越权证据条数（§4 权限范围消费方）",
)

rag_version_filtered_total = Counter(
    "rag_version_filtered_total",
    "RAG 生成上下文中被版本窗口过滤剔除的证据条数（§6 版本治理消费方，R4）",
)


feedback_total = Counter(
    "feedback_total",
    "用户反馈总数（按投票）",
    labelnames=("vote",),  # positive | negative
)

# NLI 监控（2026-08-11）：超时次数 + 当前未覆盖率
nli_timeout_total = Counter(
    "nli_timeout_total",
    "NLI 推理超时次数（触发 fallback）",
)
nli_coverage_rate = Gauge(
    "nli_coverage_rate",
    "NLI 有效校验率（0-1，1 = 无超时）",
)

# ── WhatsApp 闭环消费可观测性（Kafka 入站 → Agent → ai.reply.events）──
kafka_consumer_processed_total = Counter(
    "kafka_consumer_processed_total",
    "Kafka 入站消息消费处理总数（按结果）",
    labelnames=("topic", "status"),  # ok | duplicate | error | skipped
)

kafka_consumer_duration_seconds = Histogram(
    "kafka_consumer_duration_seconds",
    "Kafka 入站消息处理耗时（含 Agent 问答全链路）",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

ai_kafka_consumer_lag = Gauge(
    "ai_kafka_consumer_lag",
    "AI 服务 Kafka 消费者滞后消息数（按 topic/分区）",
    labelnames=("topic", "partition"),
)

# ── Input Guard 可观测性（输入侧门禁）──
input_guard_total = Counter(
    "input_guard_total",
    "Input Guard 判定总数（按动作与分类）",
    labelnames=("action", "category"),  # allow|clarify|block|degrade × 分类
)

input_guard_duration_seconds = Histogram(
    "input_guard_duration_seconds",
    "Input Guard 判定耗时（秒，按决策来源层）",
    labelnames=("layer",),  # rule | llm | fallback
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0),
)

# ── 降级/韧性告警（P1-8）──
degradation_alerts_total = Counter(
    "degradation_alerts_total",
    "降级/韧性链告警总数（按告警代码与级别）",
    labelnames=("code", "level"),  # code: LLM_CIRCUIT_OPEN / WORKER_TIMEOUT / ...
)

# 熔断器状态（0=closed 1=half_open 2=open）— 熔断开路告警数据源
circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "熔断器状态（0=closed, 1=half_open, 2=open）",
    labelnames=("name",),
)

# ── Trace 数据质量指标（2026-09-03 观测数据记录优化）──
trace_finish_total = Counter(
    "trace_finish_total",
    "完成的 trace 总数（按顶层状态，含 rejected）",
    labelnames=("status",),  # success | error | rejected
)
trace_rejection_total = Counter(
    "trace_rejection_total",
    "Evidence Gate 拒答 trace 数（按拒答层）",
    labelnames=("layer",),  # retrieval | rerank | evaluation | claim_verify | ...
)
trace_span_leak_total = Counter(
    "trace_span_leak_total",
    "未关闭即被强制收尾的 span 数（埋点泄漏）",
)
trace_uncovered_ratio = Histogram(
    "trace_uncovered_ratio",
    "trace 总耗时中无 span 归因的比例（0-1，高值 = 埋点黑洞）",
    buckets=(0.0, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0),
)
llm_usage_missing_total = Counter(
    "llm_usage_missing_total",
    "LLM 调用后 token 用量采集失败的次数",
)


def publish_breaker_states() -> None:
    """把全部熔断器状态刷到 Prometheus Gauge（周期调用或 /metrics 请求时调用）。"""
    try:
        from backend.infra.circuit_breaker import get_all_breakers
        for name, breaker in get_all_breakers().items():
            value = {"closed": 0, "half_open": 1, "open": 2}.get(breaker.state.value, 0)
            circuit_breaker_state.labels(name=name).set(value)
    except Exception:
        pass  # 指标采集失败不影响业务



# Router 监控（2026-08-11）：3 层 Router 决策可观测
router_decision_total = Counter(
    "router_decision_total",
    "Router 决策总数（按 mode 统计）",
    labelnames=("mode",),  # direct | plan | workflow
)
router_layer_total = Counter(
    "router_layer_total",
    "Router 哪一层命中（rule / embedding / llm）",
    labelnames=("layer",),
)
router_confidence = Histogram(
    "router_confidence",
    "Router 决策置信度分布（0-1）",
    buckets=(0.3, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0),
)

# ── FC 工具选择可观测性（tool_selector 节点）──
# source: fc（模型选定）| no_match（模型明确无匹配）| passthrough（直通，
#         reason 细分: flag_off/rollout_skip/fast_path/not_direct/no_candidates/
#         no_valid_candidates/schema_convert_failed/llm_failed/fc_invalid_after_retry）
tool_selector_total = Counter(
    "tool_selector_total",
    "FC 工具选择结果总数（按来源与原因）",
    labelnames=("source", "reason"),
)
tool_selector_selected_total = Counter(
    "tool_selector_selected_total",
    "FC 选定的 capability 分布（仅 source=fc 时计数）",
    labelnames=("capability",),
)
tool_selector_latency_seconds = Histogram(
    "tool_selector_latency_seconds",
    "tool_selector 节点决策耗时（含 LLM 调用）",
    buckets=(0.005, 0.05, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 17.0, 30.0),
)


def record_tool_selection(source: str, reason: str = "",
                          capability: str = "", elapsed_ms: float | None = None) -> None:
    """埋点 FC 工具选择结果（软失败不影响主流程）。"""
    try:
        tool_selector_total.labels(source=source, reason=reason or "-").inc()
        if source == "fc" and capability:
            tool_selector_selected_total.labels(capability=capability).inc()
        if elapsed_ms is not None:
            tool_selector_latency_seconds.observe(elapsed_ms / 1000.0)
    except Exception:
        pass


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

# 实时 rate（Gauge 缓存最新计算值）
rag_hit_rate = Gauge(
    "rag_hit_rate",
    "RAG 命中率（0-1）。告警阈值 < 0.7",
)
rag_reject_rate = Gauge(
    "rag_reject_rate",
    "RAG 拒答率（0-1，Evidence Gate 拦截）。告警阈值 > 0.3",
)
doc_metadata_coverage = Gauge(
    "doc_metadata_coverage",
    "活跃文档 metadata 完整度（0-1）。告警阈值 < 0.8",
)
feedback_positive_rate = Gauge(
    "feedback_positive_rate",
    "用户反馈 👍 比例（0-1）。告警阈值 < 0.7",
)


def render_metrics() -> tuple[bytes, str]:
    """生成 Prometheus 文本格式输出。

    Returns:
        (body, content_type) — 给 FastResponse 直接用
    """
    return generate_latest(), CONTENT_TYPE_LATEST


# ── 埋点 helpers（2026-08-11 新增）──
_rag_state = {"hit": 0, "rejected": 0, "fallback": 0}
_feedback_state = {"positive": 0, "negative": 0}


def record_rag_status(status: str) -> None:
    """埋点 RAG 查询结果（hit / rejected / fallback）并更新实时 rate gauge。

    使用:
        from backend.observability.metrics import record_rag_status
        record_rag_status("hit")
    """
    if status not in _rag_state:
        return
    _rag_state[status] += 1
    rag_query_total.labels(status=status).inc()
    total = _rag_state["hit"] + _rag_state["rejected"] + _rag_state["fallback"]
    if total > 0:
        rag_hit_rate.set((_rag_state["hit"] + _rag_state["fallback"]) / total)
        rag_reject_rate.set(_rag_state["rejected"] / total)


def record_feedback(vote: str) -> None:
    """埋点用户反馈（positive / negative）并更新 👍 比例。

    使用:
        from backend.observability.metrics import record_feedback
        record_feedback("positive")
    """
    if vote not in _feedback_state:
        return
    _feedback_state[vote] += 1
    feedback_total.labels(vote=vote).inc()
    total = _feedback_state["positive"] + _feedback_state["negative"]
    if total > 0:
        feedback_positive_rate.set(_feedback_state["positive"] / total)


def update_metadata_coverage() -> None:
    """扫描 doc_registry 计算活跃文档 metadata 完整度（异步调用）。

    完整定义: doc_type + business_domain + summary 都有值的 active 文档 / 总 active 文档。
    R1: 改走 DocumentRegistry（支持 SQLite/PG 双后端），原先硬编码路径的裸 sqlite
    读在 PG 模式下会读到过期数据。
    """
    try:
        from backend.config.database import DOC_REGISTRY_PATH
        from backend.rag.indexing.doc_registry import DocumentRegistry
        rows = DocumentRegistry(DOC_REGISTRY_PATH).list_active()
        total = len(rows)
        if total > 0:
            complete = sum(
                1 for r in rows
                if r.get("doc_type") is not None and r.get("doc_type") != "general"
                and r.get("business_domain") is not None and r.get("business_domain") != "general"
                and r.get("summary") is not None and r.get("summary") != ""
            )
            doc_metadata_coverage.set(complete / total)
    except Exception:
        # registry 可能尚未初始化（首次启动）
        pass


def record_router_decision(mode: str, layer: str, confidence: float) -> None:
    """埋点 Router 决策（2026-08-11）。

    Args:
        mode: execution_mode（direct / plan / workflow）
        layer: 哪一层命中（rule / embedding / llm）
        confidence: 决策置信度 0-1
    """
    try:
        router_decision_total.labels(mode=mode).inc()
        router_layer_total.labels(layer=layer).inc()
        router_confidence.observe(confidence)
    except Exception:
        pass


# ── CS 指标 helpers（Phase 6）──

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


def record_trace_finish(status: str, rejection_layer: str,
                        leaked_spans: int, uncovered_ratio: float) -> None:
    """埋点一条完成的 trace（2026-09-03）。

    Args:
        status: 顶层聚合状态（success / error / rejected）
        rejection_layer: 拒答层（仅 rejected 时非空）
        leaked_spans: 被强制收尾的未关闭 span 数
        uncovered_ratio: 无 span 归因耗时占比 0-1
    """
    try:
        trace_finish_total.labels(status=status).inc()
        if status == "rejected":
            trace_rejection_total.labels(layer=rejection_layer or "unknown").inc()
        if leaked_spans > 0:
            trace_span_leak_total.inc(leaked_spans)
        trace_uncovered_ratio.observe(max(0.0, min(1.0, uncovered_ratio)))
    except Exception:
        pass


__all__ = [
    "chat_request_total",
    "chat_request_duration_seconds",
    "llm_tokens_total",
    "skill_failure_total",
    "chat_stream_event_dropped_total",
    "chat_stream_event_produced_total",
    "chat_tpot_seconds",
    "StreamLatencyTracker",
    # 并发控制指标
    "request_concurrency_active",
    "request_concurrency_queued",
    "request_concurrency_wait_seconds",
    "request_concurrency_reject_total",
    # 运营指标
    "rag_query_total",
    "feedback_total",
    "nli_timeout_total",
    "nli_coverage_rate",
    "rag_hit_rate",
    "rag_reject_rate",
    "doc_metadata_coverage",
    "feedback_positive_rate",
    # Router 指标
    "router_decision_total",
    "router_layer_total",
    "router_confidence",
    "record_rag_status",
    "record_feedback",
    "update_metadata_coverage",
    "record_router_decision",
    "record_trace_finish",
    # FC 工具选择指标
    "tool_selector_total",
    "tool_selector_selected_total",
    "tool_selector_latency_seconds",
    "record_tool_selection",
    # CS 指标（Phase 6）
    "cs_intent_total",
    "cs_permission_violation_total",
    "cs_action_total",
    "cs_confirmation_total",
    "cs_handoff_total",
    "cs_rag_status_total",
    "record_cs_intent",
    "record_cs_permission_violation",
    "record_cs_action",
    "record_cs_confirmation",
    "record_cs_handoff",
    "record_cs_rag_status",
    "record_cs_supervisor_decision",
    "record_cs_expert_result",
    "cs_supervisor_decision_total",
    "cs_expert_result_total",
    # Trace 数据质量指标（2026-09-03）
    "trace_finish_total",
    "trace_rejection_total",
    "trace_span_leak_total",
    "trace_uncovered_ratio",
    "llm_usage_missing_total",
    "render_metrics",
]
