"""核心 SLI、Token 计费预算、并发控制与 SSE 执行池指标。"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

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


# ── Model Governance STOP C 指标（2026-09-23）──
# Label 基数红线：model/provider/status/error_type 均为有限枚举集；
# 禁止 user_id / session_id / trace_id / request_id 进 label（同下方禁令）。
llm_requests_total = Counter(
    "llm_requests_total",
    "LLM 调用次数（按登记模型 / provider / 结果）",
    labelnames=("model", "provider", "status"),  # status: ok | error
)


llm_failures_total = Counter(
    "llm_failures_total",
    "LLM 调用失败次数（按登记模型 / 错误分类）",
    labelnames=("model", "error_type"),  # error_type: error_taxonomy 分类
)


llm_fallback_total = Counter(
    "llm_fallback_total",
    "fallback 接管次数（primary 失败转备用模型成功）",
    labelnames=("primary_model", "fallback_model"),
)


token_usage_total = Counter(
    "token_usage_total",
    "Unified token usage by component, model and direction",
    labelnames=("component", "model", "direction"),  # component: embedding|rerank|llm
)


# ── SQL Agent 生产收口指标（STOP C 2026-09-23）──
# 标签基数约束（规格 §十一）：source ∈ http|graph|tool|mcp|unknown；
# decision ∈ ALLOW|DENY_*|EXECUTION_*|TIMEOUT；reason ∈ 低基数枚举。
# 禁止 user_id/tenant_id/trace/请求 ID/SQL 原文/表名进入标签。
sql_agent_requests_total = Counter(
    "sql_agent_requests_total",
    "SQL Agent 请求总数（按来源通道与最终决策）",
    labelnames=("source", "decision"),
)


sql_agent_denied_total = Counter(
    "sql_agent_denied_total",
    "SQL Agent 安全拒绝数（按低基数原因）",
    labelnames=("reason",),  # permission|scope|table|validator|unknown_scope|disabled
)


sql_agent_execution_total = Counter(
    "sql_agent_execution_total",
    "SQL 执行终态（executor 返回 status）",
    labelnames=("status",),  # success|no_data|timeout|syntax_error|permission_denied|failed
)


sql_agent_execution_duration_seconds = Histogram(
    "sql_agent_execution_duration_seconds",
    "SQL 执行耗时（秒，executor 侧）",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


sql_agent_rows_returned_total = Counter(
    "sql_agent_rows_returned_total",
    "SQL 查询返回行数累计",
)


skill_failure_total = Counter(
    "skill_failure_total",
    "Skill execution failures by skill name and error type",
    labelnames=("skill", "error_type"),
)


# ── 拒答转追问漏斗（2026-10-03 企业口径）：没有漏斗指标的追问机制等于盲调 ──
# 漏斗口径：shown（卡曝光）→ clicked（选项点击=原文重发命中）→
# resolved（点击轮以非拒答回答收尾）；unanswered 为业务拒答旁路登记量。
# source 取 clarify_content 各卡 source，禁止携带用户/租户/会话/问题原文。
agent_clarify_shown_total = Counter(
    "agent_clarify_shown_total",
    "追问卡曝光次数（按卡片来源）",
    labelnames=("source",),
)


agent_clarify_clicked_total = Counter(
    "agent_clarify_clicked_total",
    "追问卡选项点击次数（按卡片来源）",
    labelnames=("source",),
)


agent_clarify_resolved_total = Counter(
    "agent_clarify_resolved_total",
    "点击追问卡后本轮得到非拒答回答的次数（按卡片来源）",
    labelnames=("source",),
)


agent_unanswered_total = Counter(
    "agent_unanswered_total",
    "业务拒答（RAG 未命中/SQL 空结果）次数，与 ai.unanswered_questions 旁路登记同源",
    labelnames=("source",),
)


# ── 域引导（handoff）漏斗（多域隔离收官 M3，2026-10-06）────────────────
# 退役指标（实施计划 §七）：guide 模式下引导卡 曝光→点击 转化按域计数；
# target_domain 只取契约枚举（travel/customer_service），
# phase ∈ shown（后端帧下发）/ clicked（前端跳转回报），禁止携带问题原文。
agent_handoff_total = Counter(
    "agent_handoff_total",
    "主图域引导卡事件（target_domain × phase）",
    labelnames=("target_domain", "phase"),
)


# ── 安全 / 幂等 / 预算闭环指标（WP6；标签禁止携带用户、租户、Trace、请求或幂等键）──
idempotency_claim_total = Counter(
    "idempotency_claim_total",
    "幂等 claim 结果（按稳定操作名与结果）",
    labelnames=("operation", "result"),
)


idempotency_execution_total = Counter(
    "idempotency_execution_total",
    "幂等副作用执行终态（按稳定操作名与结果）",
    labelnames=("operation", "result"),
)


budget_request_total = Counter(
    "budget_request_total",
    "请求级预算门禁结果",
    labelnames=("mode", "result"),
)


budget_quota_total = Counter(
    "budget_quota_total",
    "用户/租户日月额度预占与结算结果",
    labelnames=("scope_type", "period_type", "result"),
)


budget_threshold_total = Counter(
    "budget_threshold_total",
    "用户/租户额度阈值事件",
    labelnames=("scope_type", "period_type", "threshold"),
)


budget_price_total = Counter(
    "budget_price_total",
    "模型价格表读取结果",
    labelnames=("component", "result"),
)


side_effect_budget_total = Counter(
    "side_effect_budget_total",
    "写副作用预算门禁结果",
    labelnames=("result",),
)


semantic_validation_total = Counter(
    "semantic_validation_total",
    "Tool/Skill 语义校验结果",
    labelnames=("layer", "result"),
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


# ── SSE 断线恢复（F2 Resume Protocol；§十八 Release Signals）──
# result: hit(live 续流) | finished(重放到终端) | not_found(不可恢复) | forbidden(身份不符)
sse_resume_total = Counter(
    "sse_resume_total",
    "SSE resume 请求总数（按结果）",
    labelnames=("result",),
)


sse_replay_events_total = Counter(
    "sse_replay_events_total",
    "SSE resume 重放的事件总数",
)


sse_resume_failure_total = Counter(
    "sse_resume_failure_total",
    "SSE resume 失败总数（reason: not_found | forbidden | invalid_cursor）",
    labelnames=("reason",),
)


# ── 索引一致性 Sweeper（2026-09-17 P0-1 告警闭环）──
# 五路存储对账检出的问题数：error 级持续增长 = 有数据不一致未修复，需告警
# ── 元数据管道路由指标（规划阶段 4.2/8.1，2026-09-19）──
# level: L0|L1|L2|L3（级联层）| rule_fallback（LLM 不可用降级规则链）
# outcome: hit（该层定案）| miss（该层未命中，流向下一层）| error（该层异常）
# fallback 触发率 = rule_fallback/hit 与 miss 之和，看板告警阈值 ≤ 5%（阶段 7.2）
metadata_route_total = Counter(
    "metadata_route_total",
    "Metadata pipeline routing decisions by cascade level and outcome",
    labelnames=("level", "outcome"),
)


metadata_route_latency_seconds = Histogram(
    "metadata_route_latency_seconds",
    "元数据决策路由耗时",
    labelnames=("source",),
    buckets=(0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


metadata_queue_wait_seconds = Histogram(
    "metadata_queue_wait_seconds",
    "元数据资源槽位等待耗时",
    labelnames=("resource",),
    buckets=(0.001, 0.005, 0.02, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
)


metadata_resource_wait_timeout_total = Counter(
    "metadata_resource_wait_timeout_total",
    "元数据资源槽位等待超时次数",
    labelnames=("resource",),
)


metadata_cache_total = Counter(
    "metadata_cache_total",
    "元数据决策缓存命中/未命中/错误次数",
    labelnames=("result",),
)


metadata_llm_calls_total = Counter(
    "metadata_llm_calls_total",
    "元数据 LLM 调用结果次数",
    labelnames=("result",),
)


metadata_resource_active = Gauge(
    "metadata_resource_active",
    "元数据资源当前活动槽位",
    labelnames=("resource",),
)


metadata_resource_queued = Gauge(
    "metadata_resource_queued",
    "元数据资源当前等待队列估计",
    labelnames=("resource",),
)


metadata_rule_snapshot_total = Counter(
    "metadata_rule_snapshot_total",
    "元数据规则快照治理结果次数",
    labelnames=("result",),
)


agent_rag_reconcile_inconsistent = Gauge(
    "agent_rag_reconcile_inconsistent",
    "最近一次索引对账不一致项数，包含 BM25 缺失与孤儿",
    multiprocess_mode="livemostrecent",
)


agent_rag_reconcile_source_available = Gauge(
    "agent_rag_reconcile_source_available",
    "最近一次索引对账数据源是否齐备",
    multiprocess_mode="livemostrecent",
)


agent_rag_parsing_timeout_total = Counter(
    "agent_rag_parsing_timeout_total",
    "被解析超时看门狗转为 failed 的登记行数",
)


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


# ── 分布式准入门（2026-09-30 主架构改造 P0-1）──
dist_gate_active = Gauge(
    "dist_gate_active",
    "本进程当前持有的分布式准入槽数（全局总量在 Redis zset，本指标仅为进程视角）",
)


dist_gate_reject_total = Counter(
    "dist_gate_reject_total",
    "分布式门上限拒绝（503）的请求数（按原因）",
    labelnames=("reason",),  # global_limit | tenant_limit
)


dist_gate_unavailable_total = Counter(
    "dist_gate_unavailable_total",
    "分布式门 Redis 不可用而 fail-open 放行的请求数",
)


# ── 状态键登记守卫（P1-1，2026-09-30）──
state_unknown_key_total = Counter(
    "state_unknown_key_total",
    "节点 update 写入未登记状态键的次数（该键将被 LangGraph 剥离，按节点）",
    labelnames=("node",),
)


# ── SSE 执行池观测（P1-3，2026-09-30）──
# 容量不足的外在症状只有 503/首字延迟升高，这两项指标用于定位是否池排队：
# active 持平于 max_workers 且 wait 分布右移 = 容量瓶颈信号
chat_sse_executor_active = Gauge(
    "chat_sse_executor_active",
    "chat-sse 线程池当前正在执行的 producer 数（ producer 进出对称 inc/dec）",
)


chat_sse_executor_wait_seconds = Histogram(
    "chat_sse_executor_wait_seconds",
    "producer 从提交线程池到开始执行的等待时长（近似=提交→首个事件产出前）",
    buckets=(0.005, 0.025, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
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


