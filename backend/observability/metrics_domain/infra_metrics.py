"""任务准入、Worker 运行时、Trace 质量、Tool 治理与熔断器指标。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

feedback_candidate_total = Counter(
    "feedback_candidate_total",
    "反馈评测候选审核与 promotion 结果",
    labelnames=("action", "result"),
)


# ── 域图阶段统一埋点（2026-10-06 Grafana 观测重构，Grafana 06 页）──
# 接线点：travel/graph_builder.py::_evented_node（唯一包装层）。
# stage = 图节点名（低基数固定集合），status = success | failed；
# 禁止 session_id/user_id/conversation_id 进 label。
agent_stage_total = Counter(
    "agent_stage_total",
    "业务域图阶段调用计数（按域 × 阶段 × 状态）",
    labelnames=("domain", "stage", "status"),
)


agent_stage_duration_seconds = Histogram(
    "agent_stage_duration_seconds",
    "业务域图阶段耗时（秒，按域 × 阶段）",
    labelnames=("domain", "stage"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)


feedback_total = Counter(
    "feedback_total",
    "用户反馈总数（按投票）",
    labelnames=("vote",),  # positive | negative
)


# ── Memory L3 检索/写入指标（2026-09-21 pgvector 修复配套）──
# 标签仅用低基数字段；禁止 user_id/tenant_id/conversation_id/query 作 label。
memory_retrieval_total = Counter(
    "memory_retrieval_total",
    "Memory L3 检索/写入结果总数（按状态与操作）",
    labelnames=("status", "operation"),  # status: success|failure|degraded; operation: retrieve|write
)


memory_retrieval_failure_total = Counter(
    "memory_retrieval_failure_total",
    "Memory L3 检索/写入失败总数（按操作）",
    labelnames=("operation",),  # operation: retrieve | write
)


memory_retrieval_latency_seconds = Histogram(
    "memory_retrieval_latency_seconds",
    "Memory L3 检索/写入耗时（秒，按操作）",
    labelnames=("operation",),  # operation: retrieve | write
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


# ── Memory provenance 指标（047，STOP B：Memory Production Closure）──
# 回答：今天写了多少 explicit/inferred？多少候选被接受/拒绝（按原因）？
# reason 为固定枚举（assistant_only/not_worthy/low_importance/duplicate/error），
# 禁止 content/user_id/message_id 等高基数值进 label。
memory_extraction_candidate_total = Counter(
    "memory_extraction_candidate_total",
    "L3 自动提取候选总数（通过 evidence 校验进入后续 gate 的候选）",
)


memory_extraction_rejected_total = Counter(
    "memory_extraction_rejected_total",
    "L3 提取候选拒绝总数（按拒绝原因）",
    labelnames=("reason",),  # assistant_only | not_worthy | low_importance | duplicate | error
)


memory_explicit_total = Counter(
    "memory_explicit_total",
    "用户显式通道写入的长期记忆总数（origin=explicit，如 memory_store_tool）",
)


memory_inferred_total = Counter(
    "memory_inferred_total",
    "后台自动提取写入的长期记忆总数（origin=inferred）",
)


memory_extraction_job_total = Counter(
    "memory_extraction_job_total",
    "L3 持久提取队列的投递与任务终态（低基数结果）",
    labelnames=("outcome",),  # enqueued|recovered|retry|succeeded|failed|cancelled|dispatch_failed|exhausted
)


memory_extraction_queue_backlog = Gauge(
    "memory_extraction_queue_backlog",
    "L3 持久提取任务当前积压数量",
    labelnames=("status",),  # pending|running|failed
)


memory_extraction_duration_seconds = Histogram(
    "memory_extraction_duration_seconds",
    "单个 L3 提取 worker 执行耗时（秒）",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 180.0, 600.0),
)


# ── Memory 事实版本管理指标（048，STOP C）──
# 回答：多少 duplicate/reaffirm/supersede/conflict？label 用固定 outcome 枚举，
# 禁止 memory_key/user_id/tenant_id/content 等高基数值进 label（§49）。
memory_store_outcome_total = Counter(
    "memory_store_outcome_total",
    "Memory 写入裁决结果总数（按 StoreOutcome 枚举）",
    labelnames=("outcome",),  # INSERTED|DUPLICATE|REAFFIRMED|SUPERSEDED|CONFLICT_BLOCKED_EXPLICIT|REJECTED
)


memory_supersede_total = Counter(
    "memory_supersede_total",
    "keyed 事实版本替换总数（同 key 新值 supersede 成功）",
)


memory_conflict_total = Counter(
    "memory_conflict_total",
    "同 key 冲突裁决总数（含 supersede 与 blocked，用于冲突率观测）",
)


memory_decay_records_total = Counter(
    "memory_decay_records_total",
    "Memory 衰减/归档任务处理的记录数（按动作）",
    labelnames=("action",),  # decayed | archived
)


# ── Memory 读取管线指标（STOP D：relevance gate + access semantics）──
# 回答：这轮召回多少候选？多少因相关性被拒？多少注入（global/semantic）？
# mark_accessed 是否成功？label 全部固定枚举，禁高基数（§43）。
memory_retrieval_candidate_total = Counter(
    "memory_retrieval_candidate_total",
    "L3 检索候选总数（SQL eligibility 后进入 gate 的候选）",
)


memory_retrieval_accepted_total = Counter(
    "memory_retrieval_accepted_total",
    "L3 检索接受（最终注入候选池）总数（按来源）",
    labelnames=("source",),  # global | semantic
)


memory_retrieval_rejected_total = Counter(
    "memory_retrieval_rejected_total",
    "L3 检索拒绝总数（按原因）",
    labelnames=("reason",),  # below_relevance
)


memory_access_mark_total = Counter(
    "memory_access_mark_total",
    "mark_accessed 成功批次总数（仅最终注入/工具返回的记忆）",
)


memory_access_mark_failure_total = Counter(
    "memory_access_mark_failure_total",
    "mark_accessed 失败批次总数（fail-open，不阻断主聊天）",
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


llm_usage_estimated_total = Counter(
    "llm_usage_estimated_total",
    "usage 缺失但已按本地估算结算的 LLM 调用次数（2026-10-02 估算兜底）",
)


# ── Tool 失败治理指标（core/tool_runtime，2026-09-22）──
# Label 基数控制：只含 tool / domain / status（status=ToolStatus 值），
# 禁止 user_id / request_id / session_id / error_message 进 label。
agent_tool_calls_total = Counter(
    "agent_tool_calls_total",
    "Tool 调用总数（按 tool/domain/status）",
    labelnames=("tool", "domain", "status"),
)


agent_tool_timeout_total = Counter(
    "agent_tool_timeout_total",
    "Tool 超时次数",
    labelnames=("tool", "domain"),
)


agent_tool_failure_total = Counter(
    "agent_tool_failure_total",
    "Tool 失败次数（timeout/unavailable/rate_limited/failed）",
    labelnames=("tool", "domain"),
)


agent_tool_retry_total = Counter(
    "agent_tool_retry_total",
    "Tool 重试次数",
    labelnames=("tool", "domain"),
)


agent_tool_fallback_total = Counter(
    "agent_tool_fallback_total",
    "Tool 走降级路径的次数（reason = fallback 名）",
    labelnames=("tool", "domain", "reason"),
)


agent_tool_circuit_open_total = Counter(
    "agent_tool_circuit_open_total",
    "熔断器进入 OPEN 的次数（按 tool）",
    labelnames=("tool",),
)


agent_tool_error_class_total = Counter(
    "agent_tool_error_class_total",
    "Tool 失败按统一错误七分类计数（error_class=observability.error_taxonomy 口径；"
    "success 不计）",
    labelnames=("tool", "domain", "error_class"),
)


agent_tool_unregistered_total = Counter(
    "agent_tool_unregistered_total",
    "未注册 tool_key 调用次数（策略注册表与 MCP 动态快照均无该 key，"
    "get_policy 走 DEFAULT_POLICY 兜底；探针/测试流量不计）——治理对账漂移信号",
    labelnames=("tool", "domain"),
)


agent_request_degraded_total = Counter(
    "agent_request_degraded_total",
    "业务结果为 degraded 的请求数",
    labelnames=("domain",),
)


agent_tool_latency_seconds = Histogram(
    "agent_tool_latency_seconds",
    "Tool 端到端延迟（含重试）",
    labelnames=("tool",),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0),
)


# ── 上下文预算管理指标（Context Budget Management，2026-09-22）──
# 低基数约束：label 只含 level/action/stage，禁止 session_id/user_id/turn_id
# 进 label（这些 ID 只进结构化日志与 Trace）。
context_compactions_total = Counter(
    "context_compactions_total",
    "上下文压缩发生次数（按层级与动作）",
    labelnames=("level", "action"),  # level: L1|L2|L3|L4|L5; action: tool_compact|history_trim|previous_outputs_compact|...
)


context_tokens_saved_total = Counter(
    "context_tokens_saved_total",
    "上下文压缩节省的 token 总数（按层级）",
    labelnames=("level",),
)


context_budget_overflow_total = Counter(
    "context_budget_overflow_total",
    "预算裁剪后仍超 hard budget 的次数（按阶段）",
    labelnames=("stage",),  # stage: preflight|tool_guard|micro_compact
)


context_compaction_latency_seconds = Histogram(
    "context_compaction_latency_seconds",
    "上下文压缩耗时（按层级；L5 含 LLM 摘要调用）",
    labelnames=("level",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)


context_autocompact_llm_tokens_total = Counter(
    "context_autocompact_llm_tokens_total",
    "L5 AutoCompact 摘要消耗的 LLM token（成本观测，按类型）",
    labelnames=("kind",),  # kind: prompt|completion
)


# Phase 5（2026-09-22）生产指标补齐：L5 结果分类 + ProtectedFacts 分层
context_l5_total = Counter(
    "context_l5_total",
    "L5 AutoCompact 尝试结果分类（kill switch 观测）",
    labelnames=("status", "reason"),
    # status: success|failed|disabled；reason 固定枚举，禁止 session_id/user_id
    # 入 label（reason: success|timeout|provider_error|empty_summary|db_error
    #         |stale_waterline|disabled）
)


context_protected_facts_total = Counter(
    "context_protected_facts_total",
    "ProtectedFacts 分层统计（LLM 原生保留 vs 确定性补丁）",
    labelnames=("type", "result"),
    # type 固定低基数: amount|identifier|percentage|date|url|error_code|other
    # result: extracted|preserved|patched
)


# P0-1（2026-09-23）TokenCounterRegistry 观测：模型感知计数口径可见性
context_token_counter_total = Counter(
    "context_token_counter_total",
    "TokenCounter 创建次数（按 provider/策略/是否估算口径）",
    labelnames=("provider", "strategy", "estimated"),
    # provider: openai|deepseek|qwen|...|unknown；strategy: native|compatible
    #           |calibrated|fallback；estimated: true|false（全低基数）
)


# P2-4（2026-09-23）用量分解：最近一次 preflight 的分项 token 占用（Gauge）
context_tokens_by_component = Gauge(
    "context_tokens_by_component",
    "最近一次 Prompt Preflight 的分项 token 占用（低基数 component）",
    labelnames=("component",),
    # component: system|history|previous_outputs|rag|tool_schema
)


# STOP C（2026-10-01）分项用量分布与总量：Gauge 只能表示进程内最近一次
# 写入值，并发请求下各分项甚至来自不同请求——看板不得用 Gauge 做聚合。
# 请求级分项看结构化日志/trace；分布看 Histogram，总量看 Counter。
context_tokens_by_component_tokens = Histogram(
    "context_tokens_by_component_tokens",
    "单次 Prompt Preflight 分项 token 分布（低基数 component）",
    labelnames=("component",),
    buckets=(64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536),
)


context_tokens_by_component_total = Counter(
    # 注意：prometheus_client 会剥掉 Counter 名尾的 _total —— 实际暴露的
    # 指标名是 context_component_tokens（与上面的 Gauge 同族不同名）
    "context_component_tokens_total",
    "Prompt Preflight 分项 token 累计（看板总量口径）",
    labelnames=("component",),
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


feedback_positive_rate = Gauge(
    "feedback_positive_rate",
    "用户反馈 👍 比例（0-1）。告警阈值 < 0.7",
)


_feedback_state = {"positive": 0, "negative": 0}


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


# ── 账本成本投影（2026-10-06，G2：llm_usage 唯一权威，此处仅投影）──────
# Prometheus 侧不做任何记账（无成本计数器）：这两个 gauge 由
# observability/cost_gauge 周期从 llm_usage 聚合刷新，数字永远由账本算出，
# 杜绝「指标累计 vs 账本」双轨漂移。成本口径 = ¥ 本位币（currency=CNY 原
# 值，否则按 BUDGET_FX_USD_CNY 折算），domain 归并口径见 cost_gauge_snapshot。
llm_usage_cost_cny_24h = Gauge(
    "llm_usage_cost_cny_24h",
    "账本投影：近 24h LLM 成本（¥，llm_usage 聚合，按域×模型）",
    labelnames=("domain", "model"),
)


llm_usage_cost_cny_month = Gauge(
    "llm_usage_cost_cny_month",
    "账本投影：当月累计 LLM 成本（¥，llm_usage 聚合，按域×模型）",
    labelnames=("domain", "model"),
)


task_admission_requests_total = Counter(
    "task_admission_requests_total",
    "任务准入请求总数",
    labelnames=("workflow", "stage"),  # stage 固定 execute
)


task_admission_allowed_total = Counter(
    "task_admission_allowed_total",
    "任务准入放行总数",
    labelnames=("workflow", "kind"),  # kind = new | takeover | fallback
)


task_admission_rejected_total = Counter(
    "task_admission_rejected_total",
    "任务准入拒绝总数",
    labelnames=("workflow", "scope", "reason"),
    # scope = global|tenant|user|workflow
    # reason = global_limit|tenant_limit|user_limit|workflow_limit|
    #          redis_unavailable|internal_error
)


task_admission_active_global = Gauge(
    "task_admission_active_global",
    "当前全局 admission 活跃任务数（从 store 读真值 set）",
)


task_admission_acquire_latency_seconds = Histogram(
    "task_admission_acquire_latency_seconds",
    "admission acquire 耗时（秒）",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


task_admission_release_total = Counter(
    "task_admission_release_total",
    "admission token 释放结果总数",
    labelnames=("result",),  # released | missing | owner_mismatch | error
)


task_admission_expired_total = Counter(
    "task_admission_expired_total",
    "admission token TTL 过期物理回收总数（reconcile 观测）",
)


task_admission_defer_total = Counter(
    "task_admission_defer_total",
    "满载 defer（延迟准入重投）总数",
    labelnames=("workflow",),
)


def record_admission_active_global(active: int) -> None:
    try:
        task_admission_active_global.set(max(0, int(active)))
    except Exception:
        pass


task_enqueued_total = Counter(
    "task_enqueued_total",
    "任务投递总数（initial/resume/recovery 统一口径）",
    labelnames=("workflow", "dispatch_type"),  # dispatch_type=initial|resume|recovery|retry
)


task_terminal_total = Counter(
    "task_terminal_total",
    "任务终态/出口总数（含非终态出口）",
    labelnames=("workflow", "status"),
    # status = SUCCESS|FAILED|CANCELLED|PAUSED|WAITING_USER|LEASE_LOST|
    #          RETRY_SCHEDULED（本轮以等待重试收口，非终态失败）
)


task_execution_duration_seconds = Histogram(
    "task_execution_duration_seconds",
    "worker 执行段耗时（租约认领→出口，不含排队）",
    labelnames=("workflow",),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
)


task_queue_wait_seconds = Histogram(
    "task_queue_wait_seconds",
    "排队等待时长（worker 拾取租约成功 - tasks.queued_at）",
    labelnames=("workflow",),
    buckets=(0.5, 1, 5, 10, 30, 60, 300, 900, 3600),
)


task_lease_events_total = Counter(
    "task_lease_events_total",
    "执行租约事件总数",
    labelnames=("event",),  # acquire|conflict|renew_fail
)


task_fenced_write_total = Counter(
    "task_fenced_write_total",
    "fencing 写结果总数（execution_id 条件写）",
    labelnames=("operation", "result"),  # operation=status|progress|checkpoint; result=accepted|fenced
)


task_retry_total = Counter(
    "task_retry_total",
    "任务重试调度总数（impl 层显式 retry）",
    labelnames=("workflow",),
)


task_recovery_total = Counter(
    "task_recovery_total",
    "stale 恢复 sweep 结果总数",
    labelnames=("result",),  # recovered|exhausted|reverted
)


task_authorization_denied_total = Counter(
    "task_authorization_denied_total",
    "执行时授权解析拒绝总数（fail-closed）",
    labelnames=("workflow",),
)


