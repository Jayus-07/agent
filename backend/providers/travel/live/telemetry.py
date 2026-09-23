"""providers/travel/live/telemetry.py — Provider 可观测（STOP J9 §94-§97）

指标（独立文件，prometheus_client 全局 REGISTRY 自然汇合 /metrics，先例
context_metrics / travel quality_metrics）。**标签低基数（§95）**：

  允许：provider / operation / status / fallback / cache_status / kind
  禁止：query / poi_id / lat / lng / city / tenant / user / conversation

trace 字段（§96）：provider.name/operation/status/cache_status/fallback/
duration_ms —— 记录在 span metrics，不记录 secret/完整用户 query/完整第三方
响应（§99-§102 安全红线）。

结构化事件（§97）：``[travel.provider] event=travel.provider.* ...``，与
``[travel.run]``/``[travel.quality]`` 同风格。

全部软失败：遥测绝不影响 Provider 调用与规划主链。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

travel_provider_requests_total = Counter(
    "travel_provider_requests_total",
    "Travel Provider 请求总数（按 provider/operation/status）",
    labelnames=("provider", "operation", "status"),
)

travel_provider_latency_seconds = Histogram(
    "travel_provider_latency_seconds",
    "Travel Provider 外部调用耗时（秒，P50/P95 由聚合得出）",
    labelnames=("provider", "operation"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 10.0),
)

travel_provider_errors_total = Counter(
    "travel_provider_errors_total",
    "Travel Provider 失败总数（kind=error 分类）",
    labelnames=("provider", "operation", "kind"),
)

travel_provider_cache_total = Counter(
    "travel_provider_cache_total",
    "Travel Provider 缓存结局（cache_status: hit/negative/stale/miss）",
    labelnames=("provider", "cache_status"),
)

travel_provider_fallback_total = Counter(
    "travel_provider_fallback_total",
    "Travel Provider 降级次数（fallback: estimate/stale/unresolved/disabled）",
    labelnames=("provider", "fallback"),
)

travel_provider_stale_total = Counter(
    "travel_provider_stale_total",
    "stale-if-error 生效次数",
    labelnames=("provider",),
)

travel_provider_quota_total = Counter(
    "travel_provider_quota_total",
    "Provider 软预算事件（state: soft_stopped）",
    labelnames=("provider", "state"),
)


def record_request(provider: str, operation: str, status: str,
                   latency_ms: int) -> None:
    """一次 Provider 调用结局（软失败）。"""
    try:
        travel_provider_requests_total.labels(
            provider=provider, operation=operation, status=status).inc()
        travel_provider_latency_seconds.labels(
            provider=provider, operation=operation).observe(latency_ms / 1000.0)
        if status not in ("success", "not_found", "disabled"):
            travel_provider_errors_total.labels(
                provider=provider, operation=operation, kind=status).inc()
    except Exception:  # noqa: BLE001
        pass


def record_cache(provider: str, cache_status: str) -> None:
    try:
        travel_provider_cache_total.labels(
            provider=provider, cache_status=cache_status).inc()
    except Exception:  # noqa: BLE001
        pass


def record_fallback(provider: str, fallback: str) -> None:
    try:
        travel_provider_fallback_total.labels(
            provider=provider, fallback=fallback).inc()
    except Exception:  # noqa: BLE001
        pass


def record_stale(provider: str) -> None:
    try:
        travel_provider_stale_total.labels(provider=provider).inc()
    except Exception:  # noqa: BLE001
        pass


def record_quota(provider: str, state: str = "soft_stopped") -> None:
    try:
        travel_provider_quota_total.labels(provider=provider, state=state).inc()
    except Exception:  # noqa: BLE001
        pass


def event(name: str, **fields) -> None:
    """结构化事件行（§97，软失败）。"""
    try:
        from backend.shared.logger import logger

        rendered = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info("[travel.provider] event=%s %s", name, rendered)
    except Exception:  # noqa: BLE001
        pass


def span_fields(result) -> dict:
    """ProviderResult → trace span metrics 字段（§96；不含敏感内容）。"""
    return {
        "provider.name": result.provider,
        "provider.operation": result.operation,
        "provider.status": result.status.value,
        "provider.cache_status": result.freshness.value,
        "provider.duration_ms": result.latency_ms,
    }
