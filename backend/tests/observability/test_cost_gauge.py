"""test_cost_gauge.py — 账本成本 Prometheus 投影（G2：只投影不记账）。

只 mock 外部边界（llm_usage store，不连真库）：断言 gauge 刷新值、
成本口径透传、月度/24h 窗口分流、daemon 线程幂等启动。
"""
from __future__ import annotations

import backend.observability.cost_gauge as cg


class _FakeStore:
    """cost_gauge_snapshot 按 cutoff 形态分流：月初零点 = 当月窗口。"""

    def __init__(self, rows_24h, rows_month):
        self._rows = {"24h": rows_24h, "month": rows_month}
        self.cutoffs: list[str] = []

    def cost_gauge_snapshot(self, cutoff: str):
        self.cutoffs.append(cutoff)
        return self._rows["month"] if cutoff.endswith("-01T00:00:00") else self._rows["24h"]


def test_refresh_sets_gauges(monkeypatch):
    from prometheus_client import REGISTRY

    from backend.observability import metrics

    fake = _FakeStore(
        rows_24h=[{"domain": "customer_service", "model": "proj-test-m1", "cost_cny": 1.25}],
        rows_month=[
            {"domain": "customer_service", "model": "proj-test-m1", "cost_cny": 12.5},
            {"domain": "main", "model": "proj-test-m1", "cost_cny": 0.5},
        ],
    )
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store", lambda: fake)
    n = cg.refresh_once()
    assert n == 3
    assert REGISTRY.get_sample_value(
        "llm_usage_cost_cny_24h",
        {"domain": "customer_service", "model": "proj-test-m1"}) == 1.25
    assert REGISTRY.get_sample_value(
        "llm_usage_cost_cny_month",
        {"domain": "customer_service", "model": "proj-test-m1"}) == 12.5
    assert REGISTRY.get_sample_value(
        "llm_usage_cost_cny_month",
        {"domain": "main", "model": "proj-test-m1"}) == 0.5
    # 两个窗口各拉一次账本（24h 滚动 + 当月零点），账本只读不写
    assert len(fake.cutoffs) == 2


def test_refresh_replaces_stale_series(monkeypatch):
    """二次刷新先清后设：消失的域不会残留旧值（投影=账本当前快照）。"""
    from prometheus_client import REGISTRY

    from backend.observability import metrics

    store = _FakeStore(
        rows_24h=[{"domain": "travel", "model": "proj-test-m2", "cost_cny": 3.0}],
        rows_month=[])
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store", lambda: store)
    cg.refresh_once()
    assert REGISTRY.get_sample_value(
        "llm_usage_cost_cny_24h", {"domain": "travel", "model": "proj-test-m2"}) == 3.0

    store._rows["24h"] = [{"domain": "travel", "model": "proj-test-m2", "cost_cny": 5.0}]
    cg.refresh_once()
    assert REGISTRY.get_sample_value(
        "llm_usage_cost_cny_24h", {"domain": "travel", "model": "proj-test-m2"}) == 5.0


def test_cutoff_shapes():
    """cutoff 与 llm_usage.ts（YYYY-MM-DDTHH:MM:SS.mmmZ）字典序可比。"""
    assert len(cg._utc_ts(1.0)) == 19
    assert cg._month_start_iso().endswith("-01T00:00:00")


def test_start_refresher_idempotent(monkeypatch):
    cg._thread = None
    monkeypatch.setattr(
        "backend.observability.llm_usage_store.get_llm_usage_store",
        lambda: _FakeStore([], []))
    try:
        cg.start_cost_gauge_refresher()
        first = cg._thread
        assert first is not None and first.is_alive()
        cg.start_cost_gauge_refresher()
        assert cg._thread is first  # 二次调用不重复起线程
    finally:
        cg._thread = None  # daemon 线程随进程回收，测试间不串
