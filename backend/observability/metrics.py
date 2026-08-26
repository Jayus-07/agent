"""Prometheus 指标 + /metrics 端点 — PR-0.3。

4 个核心 SLI（对齐 docs/architecture/production-readiness.md §1.3）：
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

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

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

# ── 运营指标（2026-08-11 新增）──
# 累计计数（用于计算 rates）
rag_query_total = Counter(
    "rag_query_total",
    "RAG 查询总数（按结果状态）",
    labelnames=("status",),  # hit | rejected | fallback
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


def publish_breaker_states() -> None:
    """把全部熔断器状态刷到 Prometheus Gauge（周期调用或 /metrics 请求时调用）。"""
    try:
        from backend.infra.circuit_breaker import get_all_breakers, State
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
    """
    try:
        import sqlite3
        conn = sqlite3.connect("data/doc_registry.db")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(
                CASE WHEN doc_type IS NOT NULL AND doc_type != 'general'
                     AND business_domain IS NOT NULL AND business_domain != 'general'
                     AND summary IS NOT NULL AND summary != ''
                THEN 1 ELSE 0 END
              ) AS complete
            FROM doc_registry WHERE status = 'active'
            """
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0] > 0:
            doc_metadata_coverage.set(row[1] / row[0])
    except Exception:
        # 文件可能不存在（首次启动）
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


def render_metrics() -> tuple[bytes, str]:
    """生成 Prometheus 文本格式输出。

    Returns:
        (body, content_type) — 给 FastResponse 直接用
    """
    return generate_latest(), CONTENT_TYPE_LATEST


__all__ = [
    "chat_request_total",
    "chat_request_duration_seconds",
    "llm_tokens_total",
    "skill_failure_total",
    "chat_stream_event_dropped_total",
    "chat_stream_event_produced_total",
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
    "render_metrics",
]
