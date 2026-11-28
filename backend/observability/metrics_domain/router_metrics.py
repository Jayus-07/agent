"""Router 意图分类、分层裁决与 FC 工具选择指标。"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

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


# RoutingEngine 收口（2026-10-05）后旧分层指标无人写的补埋点
# （2026-10-06 Grafana 观测重构）：证据源 → rule|embedding|llm 低基数归并，
# 未知源归 other，禁止把自由文本放进 layer。
_LAYER_ALIASES = {
    "rule": "rule", "regex": "rule", "rule_override": "rule",
    "rule_intent": "rule", "keyword": "rule",
    "vector": "embedding", "embedding": "embedding",
    "vector_index": "embedding", "vector_store": "embedding",
    # CoarseIntentClassifier（本地 embedding 向量粗分类）命中后 source=classifier
    # ——「embedding 层命中」的真实仓库枚举（2026-10-07 补验②修正）
    "classifier": "embedding",
    "llm": "llm", "llm_selector": "llm", "llm_fallback": "llm",
    "llm_intent": "llm",
}


def record_routing_engine_decision(
    *,
    mode: str,
    source: str,
    domain: str,
    confidence: float,
    verdict: str,
    latency_ms: float,
) -> None:
    """RoutingEngine 每次决策的统一观测出口（engine.route() 出口调用；软失败）。

    复活收口后无人写的看板指标：router_decision_total / router_layer_total /
    router_confidence / routing_domain_total / routing_hierarchy_verdict_total
    + routing_latency_seconds{stage=hierarchical_total}。只做记账，
    不参与任何路由决策。
    """
    try:
        layer = _LAYER_ALIASES.get(str(source or "").lower(), "other")
        router_decision_total.labels(mode=str(mode or "unknown")).inc()
        router_layer_total.labels(layer=layer).inc()
        router_confidence.observe(min(max(float(confidence or 0.0), 0.0), 1.0))
        routing_domain_total.labels(
            domain=str(domain or "unknown"), source=layer).inc()
        routing_hierarchy_verdict_total.labels(verdict=str(verdict or "-")).inc()
        routing_latency_seconds.labels(stage="hierarchical_total").observe(
            max(float(latency_ms or 0), 1) / 1000)
    except Exception:
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


