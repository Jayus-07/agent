"""MetricsCollector — rolling-window latency + recall statistics.

Stores aggregate values only (p50/p95/p99). Never stores raw query text.
Designed for: Prometheus scraping, CLI inspection, evaluation feedback.
"""

import time
import math
from collections import deque
from dataclasses import dataclass, field
from contextlib import contextmanager


@dataclass
class RetrievalMetrics:
    """Per-request timing breakdown."""

    intent: str = ""
    retrieval_time_ms: float = 0.0
    rerank_time_ms: float = 0.0
    llm_time_ms: float = 0.0
    total_time_ms: float = 0.0
    recalled_chunks: int = 0
    final_chunks: int = 0
    filter_applied: bool = False


class MetricsCollector:
    """Global singleton. Rolling windows of last N requests."""

    def __init__(self, window_size: int = 100):
        self._total_ms = deque(maxlen=window_size)
        self._retrieval_ms = deque(maxlen=window_size)
        self._rerank_ms = deque(maxlen=window_size)
        self._llm_ms = deque(maxlen=window_size)
        self._recalled = deque(maxlen=window_size)
        self._final = deque(maxlen=window_size)
        self._filter_uses = deque(maxlen=window_size)  # bool

    def record(self, m: RetrievalMetrics) -> None:
        self._total_ms.append(m.total_time_ms)
        self._retrieval_ms.append(m.retrieval_time_ms)
        self._rerank_ms.append(m.rerank_time_ms)
        self._llm_ms.append(m.llm_time_ms)
        self._recalled.append(m.recalled_chunks)
        self._final.append(m.final_chunks)
        self._filter_uses.append(1 if m.filter_applied else 0)

    def summary(self) -> dict:
        if not self._total_ms:
            return {"count": 0}

        def _p(values, pct):
            if not values:
                return 0.0
            s = sorted(values)
            idx = int(len(s) * pct / 100)
            return s[min(idx, len(s) - 1)]

        n = len(self._total_ms)
        return {
            "count": n,
            "avg_total_ms": round(sum(self._total_ms) / n, 1),
            "avg_retrieval_ms": round(sum(self._retrieval_ms) / n, 1),
            "avg_rerank_ms": round(sum(self._rerank_ms) / n, 1),
            "avg_llm_ms": round(sum(self._llm_ms) / n, 1),
            "p50_total_ms": round(_p(list(self._total_ms), 50), 1),
            "p95_total_ms": round(_p(list(self._total_ms), 95), 1),
            "p99_total_ms": round(_p(list(self._total_ms), 99), 1),
            "avg_recalled": round(sum(self._recalled) / n, 1),
            "avg_final": round(sum(self._final) / n, 1),
            "filter_usage_rate": round(sum(self._filter_uses) / n, 2),
        }

    @property
    def count(self) -> int:
        return len(self._total_ms)


# Global singleton
metrics_collector = MetricsCollector()


@contextmanager
def timed(metrics: RetrievalMetrics, field: str):
    """Usage: with timed(m, 'rerank_time_ms'): do_rerank()"""
    t0 = time.perf_counter()
    yield
    setattr(metrics, field, (time.perf_counter() - t0) * 1000)
