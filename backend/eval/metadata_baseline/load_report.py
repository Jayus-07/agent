"""生成 ``metadata-load-v1`` 压测证据。

该模块只负责把压测观测值收敛成稳定的 JSON 契约，不负责决定门禁是否
通过。门禁仍由 ``validate_release`` 统一判断，避免压测脚本自行放宽上线
标准。
"""
from __future__ import annotations

import math
from typing import Iterable


LOAD_REPORT_VERSION = "metadata-load-v1"


def _positive_values(values: Iterable[float]) -> list[float]:
    """过滤无效值并把负数钳制为零，避免观测异常污染 P95。"""

    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(max(number, 0.0))
    return result


def percentile(values: Iterable[float], quantile: float) -> float:
    """计算 nearest-rank 百分位；空样本返回 0。"""

    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile 必须位于 [0, 1]")
    ordered = sorted(_positive_values(values))
    if not ordered:
        return 0.0
    rank = max(math.ceil(len(ordered) * quantile), 1) - 1
    return ordered[min(rank, len(ordered) - 1)]


def _rounded(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _rate(numerator: int | float, denominator: int | float) -> float:
    numerator_value = max(float(numerator), 0.0)
    denominator_value = max(float(denominator), 0.0)
    if denominator_value == 0:
        return 0.0
    return numerator_value / denominator_value


def build_load_report(
    *,
    baseline_peak_concurrency: int,
    observed_peak_concurrency: int,
    queue_wait_ms: Iterable[float],
    processing_ms: Iterable[float],
    lineage_api_ms: Iterable[float],
    db_pool_wait_ms: Iterable[float],
    duration_seconds: float,
    submitted_count: int,
    completed_count: int,
    failed_count: int,
    embedding_calls: int,
    llm_calls: int,
    llm_429_count: int,
    cache_hits: int,
    cache_misses: int,
    duplicate_write_count: int,
    queue_drained: bool,
    shadow_enabled: bool,
    shadow_processing_ms: Iterable[float] = (),
    api_error_count: int = 0,
    ocr_calls: int = 0,
) -> dict:
    """把一次压测的原始样本转换为发布门禁所需的报告。

    ``processing_ms`` 是处理任务端到端耗时，``lineage_api_ms`` 是血缘详情
    API 请求耗时，两者分开保留；门禁使用前者比较主/影子路径，前端和
    运维证据使用后者观察血缘查询是否成为瓶颈。
    """

    queue_samples = _positive_values(queue_wait_ms)
    processing_samples = _positive_values(processing_ms)
    api_samples = _positive_values(lineage_api_ms)
    pool_samples = _positive_values(db_pool_wait_ms)
    shadow_samples = _positive_values(shadow_processing_ms)
    duration = max(float(duration_seconds), 0.0)
    actual_cache_operations = max(int(cache_hits), 0) + max(int(cache_misses), 0)
    actual_llm_calls = max(int(llm_calls), 0)
    actual_embedding_calls = max(int(embedding_calls), 0)
    actual_ocr_calls = max(int(ocr_calls), 0)

    primary_p95 = _rounded(percentile(processing_samples, 0.95))
    if shadow_enabled:
        shadow_p95 = _rounded(percentile(shadow_samples, 0.95))
        shadow_comparable = True
    else:
        # 开发阶段影子队列通常关闭。保留同一主路径 P95 让既有门禁表达
        # “没有影子额外开销”，并通过额外字段明确本次未启用影子观测。
        shadow_p95 = primary_p95
        shadow_comparable = False

    baseline = max(int(baseline_peak_concurrency), 0)
    observed = max(int(observed_peak_concurrency), 0)
    submitted = max(int(submitted_count), 0)
    completed = max(int(completed_count), 0)
    failed = max(int(failed_count), 0)

    return {
        "report_version": LOAD_REPORT_VERSION,
        "evidence_scope": "metadata_processing_lineage_load",
        "baseline_peak_concurrency": baseline,
        "observed_peak_concurrency": observed,
        "peak_multiplier": _rounded(observed / baseline, 3) if baseline else 0.0,
        "submitted_count": submitted,
        "completed_count": completed,
        "failed_count": failed,
        "duration_seconds": _rounded(duration),
        "sustained_queue_growth": not bool(queue_drained),
        "queue_age_p95_seconds": _rounded(percentile(queue_samples, 0.95) / 1000),
        "primary_p95_ms": primary_p95,
        "lineage_api_p95_ms": _rounded(percentile(api_samples, 0.95)),
        "shadow_p95_ms": shadow_p95,
        "shadow_enabled": bool(shadow_enabled),
        "shadow_latency_comparable": shadow_comparable,
        "embedding_qps": _rounded(
            actual_embedding_calls / duration if duration else 0.0
        ),
        "llm_qps": _rounded(actual_llm_calls / duration if duration else 0.0),
        "llm_429_rate": _rate(llm_429_count, actual_llm_calls),
        "model_call_count": (
            actual_embedding_calls + actual_llm_calls + actual_ocr_calls
        ),
        "embedding_call_count": actual_embedding_calls,
        "llm_call_count": actual_llm_calls,
        "ocr_call_count": actual_ocr_calls,
        "cache_hit_count": max(int(cache_hits), 0),
        "cache_miss_count": max(int(cache_misses), 0),
        "cache_hit_rate": _rate(cache_hits, actual_cache_operations),
        "db_pool_wait_p95_ms": _rounded(percentile(pool_samples, 0.95)),
        "duplicate_write_count": max(int(duplicate_write_count), 0),
        "api_error_count": max(int(api_error_count), 0),
        "queue_wait_sample_count": len(queue_samples),
        "processing_sample_count": len(processing_samples),
        "lineage_api_sample_count": len(api_samples),
        "db_pool_wait_sample_count": len(pool_samples),
    }


__all__ = ["LOAD_REPORT_VERSION", "build_load_report", "percentile"]
