"""聚合/性能/稳定性指标 — p95 延迟 / 稳定性方差 / 均值聚合。"""
import math
import statistics
from itertools import combinations
from typing import Any

from backend.evaluation.metrics.retrieval import _string_jaccard


def p95_latency(durations_ms: list[int]) -> int:
    """95 分位响应时间（毫秒）。"""
    if not durations_ms:
        return 0
    sorted_d = sorted(durations_ms)
    idx = int(len(sorted_d) * 0.95)
    idx = min(idx, len(sorted_d) - 1)
    return int(sorted_d[idx])


def stability_variance(answers: list[str]) -> float:
    """同问 N 次答案的稳定性方差 — 越小越稳定。"""
    if len(answers) < 2:
        return 0.0
    pairs = [
        _string_jaccard(a, b) for a, b in combinations(answers, 2)
    ]
    return float(statistics.pvariance(pairs))


def aggregate_metrics(results: list[Any]) -> dict[str, float]:
    """从 EvalResult 列表聚合统计指标（per-case 指标的均值）。"""
    agg: dict[str, list[float]] = {}
    for r in results:
        for k, v in (r.metrics or {}).items():
            if isinstance(v, (int, float)) and not math.isnan(v):
                agg.setdefault(k, []).append(float(v))
    return {k: round(sum(vs) / len(vs), 4) for k, vs in agg.items() if vs}
