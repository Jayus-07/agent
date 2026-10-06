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

import os as _os

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# multiprocess 目录必须在任何指标定义前存在（prometheus_client 创建指标时
# 即写 mmap 文件；worker 内 import 顺序不可控，故在本模块顶部自建）。
# app 进程未设 PROMETHEUS_MULTIPROC_DIR → no-op（单进程默认 registry）。
_mp_dir = _os.getenv("PROMETHEUS_MULTIPROC_DIR", "")
if _mp_dir:
    try:
        _os.makedirs(_mp_dir, exist_ok=True)
    except Exception:  # noqa: BLE001 — 创建失败由单指标创建异常暴露
        pass

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
# target_domain 只取契约枚举（travel/customer_service/selection_funnel），
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
feedback_candidate_total = Counter(
    "feedback_candidate_total",
    "反馈评测候选审核与 promotion 结果",
    labelnames=("action", "result"),
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

metadata_classifier_mismatch_total = Counter(
    "metadata_classifier_mismatch_total",
    "元数据分类器模型卡与当前运行时契约不一致的次数",
    labelnames=("reason",),
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

metadata_shadow_dispatch_total = Counter(
    "metadata_shadow_dispatch_total",
    "元数据影子任务派发结果次数",
    labelnames=("result",),
)

metadata_shadow_job_total = Counter(
    "metadata_shadow_job_total",
    "元数据影子任务终态次数",
    labelnames=("result",),
)

metadata_shadow_queue_age_seconds = Histogram(
    "metadata_shadow_queue_age_seconds",
    "元数据影子任务排队年龄",
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 300.0, 900.0),
)

metadata_shadow_latency_seconds = Histogram(
    "metadata_shadow_latency_seconds",
    "元数据影子任务执行耗时",
    buckets=(0.05, 0.1, 0.5, 1.0, 3.0, 5.0, 10.0, 30.0),
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

    @property
    def ttft_ms(self) -> int | None:
        """首 token 延迟毫秒（M13 尾项：落 trace_summary.ttft_ms 用）。

        None = 未收到任何 delta（流式前失败/纯 status 流），调用方跳过落库。
        """
        if self._first_delta is None:
            return None
        return max(1, round((self._first_delta - self._start) * 1000))

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

# ── 运营指标（2026-08-11 新增）──
# 累计计数（用于计算 rates）
rag_query_total = Counter(
    "rag_query_total",
    "RAG 查询总数（按结果状态）",
    labelnames=("status",),  # hit | rejected | fallback
)

rag_upload_total = Counter(
    "rag_upload_total",
    "RAG 上传索引终态总数（按结果状态）",
    labelnames=("status",),  # success | duplicate | failed
)

rag_index_duration_seconds = Histogram(
    "rag_index_duration_seconds",
    "RAG 文档从索引开始到终态的耗时（秒）",
    labelnames=("status",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
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

# ── RAG 阶段时延（2026-10-06 Grafana 观测重构，Grafana 04 页长期时序）──
# stage 固定低基数枚举，禁止扩展：
#   retrieve = 检索主阶段（BM25+向量+同文档扩展，到 context 就绪为止，含内部 rerank）
#   rerank   = RerankCompressor 压缩耗时（retrieve 的子阶段）
#   generate = LLM 生成（context 就绪 → chain.invoke 返回）
#   total    = RAGChain.ask() 全链（含 verify/gate）
rag_stage_duration_seconds = Histogram(
    "rag_stage_duration_seconds",
    "RAG 阶段耗时（秒，按固定低基数 stage 枚举）",
    labelnames=("stage",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160),
)


def record_rag_stage(stage: str, duration_ms: float) -> None:
    """rag_stage_duration_seconds 记账入口（挂点跨文件复用；软失败）。

    stage 固定枚举 retrieve | rerank | generate | total，调用方传入其他值
    会扩张 Prometheus 序列——此处不做白名单拦截以保持旁路零开销，
    新 stage 值必须先改上面 Histogram 注释与 Grafana 面板。
    """
    try:
        rag_stage_duration_seconds.labels(stage=stage).observe(
            max(float(duration_ms), 1) / 1000)
    except Exception:  # noqa: BLE001 — 指标旁路软失败
        pass

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


# ── 分层路由可观测性（hierarchical routing，2026-09-22）──
# 评估口径（§19）：域分类分布 / unknown rate / LLM selector usage rate /
# fast path rate / clarification rate / shadow match rate / 路由耗时
routing_domain_total = Counter(
    "routing_domain_total",
    "粗分类域分布（含 unknown；source=rule/classifier/gate）",
    labelnames=("domain", "source"),
)
routing_hierarchy_verdict_total = Counter(
    "routing_hierarchy_verdict_total",
    "分层路由裁决分布（fast_path / llm_selector / clarification / "
    "rule_workflow / rule_composite / prefilter_* / plan）",
    labelnames=("verdict",),
)
routing_latency_seconds = Histogram(
    "routing_latency_seconds",
    "分层路由分阶段耗时（coarse / fine / total，秒）",
    labelnames=("stage",),
    buckets=(0.001, 0.005, 0.01, 0.03, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)
router_fallback_total = Counter(
    "router_fallback_total",
    "统一路由引擎受控降级次数",
    labelnames=("reason",),
)
router_cache_total = Counter(
    "router_cache_total",
    "统一路由引擎缓存结果",
    labelnames=("result",),  # hit | miss
)


def record_domain_classification(domain: str, source: str) -> None:
    """埋点粗分类结果（domain=unknown 即 unknown rate 分子）。"""
    try:
        routing_domain_total.labels(domain=domain, source=source or "-").inc()
    except Exception:
        pass


def record_hierarchy_verdict(verdict: str) -> None:
    """埋点分层路由裁决（fast path / llm selector / clarification / ...）。"""
    try:
        routing_hierarchy_verdict_total.labels(verdict=verdict or "-").inc()
    except Exception:
        pass


def record_routing_latency(stage: str, elapsed_ms: float) -> None:
    """埋点分层路由分阶段耗时（毫秒入参，秒入桶）。"""
    try:
        routing_latency_seconds.labels(stage=stage or "-").observe(elapsed_ms / 1000.0)
    except Exception:
        pass


def record_router_fallback(reason: str) -> None:
    """记录低基数的路由降级原因。"""

    allowed = {
        "domain_classifier_degraded",
        "domain_router_error",
        "capability_router_error",
        "vector_unavailable",
        "vector_index_mismatch",
        "vector_query_error",
        "llm_failure",
    }
    label = reason.split(":", 1)[0] if reason else "unknown"
    if label not in allowed:
        label = "unknown"
    try:
        router_fallback_total.labels(reason=label).inc()
    except Exception:
        pass


def record_router_cache(result: str) -> None:
    """记录路由缓存命中/未命中。"""

    try:
        router_cache_total.labels(
            result=result if result in {"hit", "miss"} else "miss",
        ).inc()
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


# ── 客服高并发派单指标（P8，方案 §六）──────────────────────

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


# ── 客服质检日报指标（批次D，2026-09-22）──────────────────

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


# ── 任务 Admission Control 指标（Phase2 Step4，2026-09-23）────
# 基数控制：workflow / scope / reason / kind / result / stage 均为固定
# 低基数词表；tenant_id / user_id / task_id 禁止作为 label（高基数）。

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


# ── 任务运行时指标（Phase2-F Worker Observability，2026-09-23）────
# 基数控制：workflow / queue / status / reason / event / operation /
# result / dispatch_type 全部为受控枚举；task_id / execution_id /
# user_id / tenant_id / trace_id 禁止作为 label（高基数，走结构化日志
# 与 trace attribute）。错误原因必须用 error_taxonomy 词表，禁止
# str(exc) 入 label。
#
# 进程归属：task_enqueued_total 在投递进程计数（API 进程走 app /metrics；
# resume/recovery 重投在 worker 计数走 worker 端点），其余在 worker
# 进程计数，经 worker metrics 端点（multiprocess 聚合）暴露。

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


__all__ = [
    "chat_request_total",
    "chat_request_duration_seconds",
    "llm_tokens_total",
    "skill_failure_total",
    "idempotency_claim_total",
    "idempotency_execution_total",
    "budget_request_total",
    "budget_quota_total",
    "budget_threshold_total",
    "budget_price_total",
    "side_effect_budget_total",
    "semantic_validation_total",
    "feedback_candidate_total",
    "chat_stream_event_dropped_total",
    "chat_stream_event_produced_total",
    "sse_resume_total",
    "sse_replay_events_total",
    "sse_resume_failure_total",
    "chat_tpot_seconds",
    "StreamLatencyTracker",
    # 并发控制指标
    "request_concurrency_active",
    "request_concurrency_queued",
    "request_concurrency_wait_seconds",
    "request_concurrency_reject_total",
    # 分布式准入门（P0-1）
    "dist_gate_active",
    "dist_gate_reject_total",
    "dist_gate_unavailable_total",
    # 状态键登记守卫（P1-1）
    "state_unknown_key_total",
    # SSE 执行池观测（P1-3）
    "chat_sse_executor_active",
    "chat_sse_executor_wait_seconds",
    # RAG 阶段时延 + 域图阶段统一埋点（2026-10-06 Grafana 观测重构）
    "rag_stage_duration_seconds",
    "record_rag_stage",
    "agent_stage_total",
    "agent_stage_duration_seconds",
    # 任务 Admission Control（Phase2 Step4）
    "task_admission_requests_total",
    "task_admission_allowed_total",
    "task_admission_rejected_total",
    "task_admission_active_global",
    "task_admission_acquire_latency_seconds",
    "task_admission_release_total",
    "task_admission_expired_total",
    "task_admission_defer_total",
    "record_admission_active_global",
    # 任务运行时（Phase2-F Worker Observability）
    "task_enqueued_total",
    "task_terminal_total",
    "task_execution_duration_seconds",
    "task_queue_wait_seconds",
    "task_lease_events_total",
    "task_fenced_write_total",
    "task_retry_total",
    "task_recovery_total",
    "task_authorization_denied_total",
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
    "router_fallback_total",
    "router_cache_total",
    "record_rag_status",
    "record_feedback",
    "update_metadata_coverage",
    "record_router_decision",
    "record_router_fallback",
    "record_router_cache",
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
    # CS 派单/outbox 指标（P8）
    "cs_dispatch_queue_depth",
    "cs_dispatch_offered_total",
    "cs_dispatch_wait_seconds",
    "cs_dispatch_reaped_total",
    "cs_dispatch_online_agents",
    "cs_outbox_pending",
    "cs_outbox_lag_seconds",
    "cs_outbox_published_total",
    "record_cs_dispatch_result",
    "record_cs_dispatch_wait",
    "record_cs_reaped",
    "set_cs_queue_depth",
    "set_cs_online_agents",
    "set_cs_outbox_pending",
    "set_cs_outbox_lag",
    "record_cs_outbox_publish",
    # 客服质检日报（批次D）
    "cs_qa_daily_conversations",
    "cs_qa_daily_handoff_rate",
    "cs_qa_daily_satisfaction",
    "record_cs_qa_conversations",
    "record_cs_qa_satisfaction",
    # Trace 数据质量指标（2026-09-03）
    "trace_finish_total",
    "trace_rejection_total",
    "trace_span_leak_total",
    "trace_uncovered_ratio",
    "llm_usage_missing_total",
    "llm_usage_estimated_total",
    # Tool 失败治理指标（2026-09-22）
    "agent_tool_calls_total",
    "agent_tool_timeout_total",
    "agent_tool_failure_total",
    "agent_tool_retry_total",
    "agent_tool_fallback_total",
    "agent_tool_circuit_open_total",
    "agent_request_degraded_total",
    "agent_tool_latency_seconds",
    "render_metrics",
]
