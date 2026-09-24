"""travel/commerce/telemetry.py — Commerce 可观测（STOP K6，任务书 §十九）

指标独立文件，prometheus_client 全局 REGISTRY 自然汇合 /metrics（先例：
providers/travel/live/telemetry.py 的 travel_provider_*）。

**标签低基数（G23）**：
  允许：commerce_type(hotel|flight) / provider / status / cache_status /
        freshness / fallback / outcome
  禁止：hotel_name / flight_number / user_id / session_id / query /
        city / offer_id（与 provider 层同一红线，§95）

trace 字段（G21）：commerce.type/provider/operation/status/offer_count/
cache_status/freshness/fallback/duration_ms——不含 secret/完整用户 query/
完整第三方响应（§99-§102 同源红线）。

全部软失败：遥测绝不影响 Commerce 调用主链。
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

travel_commerce_requests_total = Counter(
    "travel_commerce_requests_total",
    "Travel Commerce 搜索请求总数（按类型/Provider/结局）",
    labelnames=("commerce_type", "provider", "status"),
)

travel_commerce_offers_total = Counter(
    "travel_commerce_offers_total",
    "Commerce 返回 offer 总数（含逐条拒绝计数 reason 非空时）",
    labelnames=("commerce_type", "provider", "outcome"),
)

travel_commerce_empty_total = Counter(
    "travel_commerce_empty_total",
    "Commerce 空结果计数（reason: empty|not_found|all_rejected）",
    labelnames=("commerce_type", "provider", "reason"),
)

travel_commerce_fallback_total = Counter(
    "travel_commerce_fallback_total",
    "Commerce 降级计数（fallback: stale/unavailable/disabled）",
    labelnames=("commerce_type", "provider", "fallback"),
)

travel_commerce_price_snapshot_total = Counter(
    "travel_commerce_price_snapshot_total",
    "价格快照产出计数（outcome: emitted）",
    labelnames=("commerce_type",),
)

travel_commerce_deeplink_total = Counter(
    "travel_commerce_deeplink_total",
    "Deep Link 校验结局（outcome: pass/rejected/absent）",
    labelnames=("commerce_type", "outcome"),
)

travel_commerce_latency_seconds = Histogram(
    "travel_commerce_latency_seconds",
    "Commerce 搜索端到端耗时（秒，含缓存与归一化）",
    labelnames=("commerce_type",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0),
)


def record_request(commerce_type: str, provider: str, status: str,
                   latency_ms: int) -> None:
    """一次搜索请求结局（软失败）。status ∈ success|empty|not_found|
    timeout|unavailable|rate_limited|invalid_response|disabled|unauthorized"""
    try:
        travel_commerce_requests_total.labels(
            commerce_type=commerce_type, provider=provider,
            status=status).inc()
        travel_commerce_latency_seconds.labels(
            commerce_type=commerce_type).observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass


def record_offers(commerce_type: str, provider: str, count: int) -> None:
    try:
        travel_commerce_offers_total.labels(
            commerce_type=commerce_type, provider=provider,
            outcome="emitted").inc(count)
    except Exception:  # noqa: BLE001
        pass


def record_rejects(commerce_type: str, provider: str, count: int) -> None:
    try:
        travel_commerce_offers_total.labels(
            commerce_type=commerce_type, provider=provider,
            outcome="rejected").inc(count)
    except Exception:  # noqa: BLE001
        pass


def record_empty(commerce_type: str, provider: str, reason: str) -> None:
    try:
        travel_commerce_empty_total.labels(
            commerce_type=commerce_type, provider=provider,
            reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


def record_fallback(commerce_type: str, provider: str, fallback: str) -> None:
    try:
        travel_commerce_fallback_total.labels(
            commerce_type=commerce_type, provider=provider,
            fallback=fallback).inc()
    except Exception:  # noqa: BLE001
        pass


def record_snapshots(commerce_type: str, count: int) -> None:
    try:
        travel_commerce_price_snapshot_total.labels(
            commerce_type=commerce_type).inc(count)
    except Exception:  # noqa: BLE001
        pass


def record_deeplink(commerce_type: str, outcome: str, count: int = 1) -> None:
    try:
        travel_commerce_deeplink_total.labels(
            commerce_type=commerce_type, outcome=outcome).inc(count)
    except Exception:  # noqa: BLE001
        pass


def event(name: str, **fields) -> None:
    """结构化事件行（与 [travel.provider] 同风格；软失败）。"""
    try:
        from backend.shared.logger import logger

        rendered = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info("[travel.commerce] event=%s %s", name, rendered)
    except Exception:  # noqa: BLE001
        pass
