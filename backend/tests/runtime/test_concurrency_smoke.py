# -*- coding: utf-8 -*-
"""STOP D：并发门的本地受控 smoke（不触碰共享 Docker/数据库服务）。"""

import asyncio
import time

import pytest

from backend.app.api.middleware.concurrency import _PriorityGate


def _percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[index]


async def _run_workload(count: int) -> dict:
    gate = _PriorityGate(max_concurrent=10)
    latencies = []
    errors = 0

    async def worker():
        nonlocal errors
        started = time.perf_counter()
        acquired = await gate.acquire("normal", timeout=2)
        if not acquired:
            errors += 1
            return
        try:
            await asyncio.sleep(0.001)
        finally:
            await gate.release()
        latencies.append((time.perf_counter() - started) * 1000)

    await asyncio.gather(*(worker() for _ in range(count)))
    return {
        "count": count,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "error_rate": errors / count,
        "active": gate._active,
        "queued": len(gate._waiters),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [10, 50, 100])
async def test_controlled_concurrency_has_no_leak_and_exposes_percentiles(count):
    stats = await _run_workload(count)
    assert stats["count"] == count
    assert stats["p50_ms"] <= stats["p95_ms"] <= stats["p99_ms"]
    assert stats["error_rate"] == 0
    assert stats["active"] == 0
    assert stats["queued"] == 0


@pytest.mark.asyncio
async def test_queue_timeout_returns_false_without_slot_leak():
    gate = _PriorityGate(max_concurrent=1)
    assert await gate.acquire("normal", timeout=0.01)
    started = time.perf_counter()
    assert await gate.acquire("normal", timeout=0.01) is False
    assert (time.perf_counter() - started) < 0.5
    assert len(gate._waiters) == 0
    await gate.release()
    assert gate._active == 0


@pytest.mark.asyncio
async def test_high_priority_waiter_is_served_before_normal_waiter():
    gate = _PriorityGate(max_concurrent=1)
    assert await gate.acquire("normal", timeout=0.1)
    order = []

    async def waiter(priority):
        acquired = await gate.acquire(priority, timeout=1)
        assert acquired
        order.append(priority)
        await gate.release()

    normal = asyncio.create_task(waiter("normal"))
    high = asyncio.create_task(waiter("high"))
    await asyncio.sleep(0.01)
    await gate.release()
    await asyncio.gather(normal, high)
    assert order == ["high", "normal"]
    assert gate._active == 0
