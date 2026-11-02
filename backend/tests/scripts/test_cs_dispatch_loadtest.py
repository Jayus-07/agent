"""cs_dispatch_loadtest 修正版回归（Q4 2026-11-03）。

锁定三件事：
  1. 延迟分位数只统计 2xx，错误（4xx/5xx/异常）独立计数不混入；
  2. 小样本 p95/p99 索引不越界；
  3. 持续负载为 open-loop 定时发射——短窗口内实际发射速率应接近目标，
     而不是旧串行实现的 1/延迟。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from scripts.cs_dispatch_loadtest import run_sustained, summarize


def test_summary_excludes_errors_from_latency_percentiles():
    records = [(0.10 * i, 200) for i in range(1, 101)]  # 0.1s..10.0s
    records.append((0.001, 429))   # 网关限流
    records.append((99.0, 500))    # 服务端错误
    records.append((98.0, "ConnectError"))
    s = summarize(records)
    assert s["total"] == 103
    assert s["success"] == 100
    assert s["success_ratio"] == round(100 / 103, 4)
    assert s["errors"] == {"429": 1, "500": 1, "ConnectError": 1}
    # 99s/98s 的错误耗时不得污染成功延迟分位数
    assert s["max_s"] == 10.0
    assert s["p95_s"] == 9.5


def test_summary_small_sample_indices_do_not_underflow():
    s = summarize([(0.5, 200)])
    assert s["p95_s"] == 0.5 and s["p99_s"] == 0.5 and s["count"] == 1
    empty = summarize([])
    assert empty["count"] == 0 and empty["success"] == 0
    assert "p95_s" not in empty


@pytest.mark.parametrize("rps,duration", [(20, 2), (50, 1)])
async def test_sustained_emits_near_target_rate(rps, duration):
    """open-loop 发射速率必须接近目标（旧串行实现达不到）。"""

    class _SlowClient:
        """每个请求耗 50ms 的最小桩（不触网）。"""

        def __init__(self):
            self.calls = 0

        async def get(self, url, headers=None):  # noqa: ARG002
            self.calls += 1
            await asyncio.sleep(0.05)

            class _Resp:
                status_code = 200

            return _Resp()

    client = _SlowClient()
    start = time.perf_counter()
    records, emit_rps, _finish_rps = await run_sustained(
        client, "http://stub", {}, rps=rps, duration_s=duration,
    )
    wall = time.perf_counter() - start
    # 串行实现此时只能发出 ~wall/0.05 个请求（20rps×2s 串行=8 个），
    # open-loop 应发出接近 rps×duration 的量
    assert len(records) == pytest.approx(rps * duration, rel=0.25)
    assert emit_rps == pytest.approx(rps, rel=0.3)
    # open-loop 不等待响应：总耗时受发射节拍主导，远短于串行总耗时
    assert wall < duration + len(records) * 0.05 + 1.0
    assert all(status == 200 for _lat, status in records)
