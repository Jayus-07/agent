# -*- coding: utf-8 -*-
"""GPT 复核补验 2（2026-10-12）：45s 超时配置下的并发/残留线程/重复调用/熔断。

验证口径（等比缩放，同一 executor 执行路径；生产 45s:300s ↔ 测试 0.5s:1.5s）：
- 单请求治理窗口 = policy timeout（超时即返回；retries=0，治理层不叠加），
  但「请求最大耗时 45s」的说法不成立——超时后 to_thread 残留线程继续在途，
  生产上限 = rag client read 300s；
- bulkhead 槽位在超时返回后立即回收（后续/重复调用可进入真实执行，
  不会被 TOOL_BUSY 拒绝）；残留的是 to_thread 线程而非 bulkhead 槽位；
- to_thread 残留线程存活时长 = 子任务剩余在途时长，随子任务完成自然结束；
- 连续 5 次超时 → 熔断 OPEN（cb_recovery 30s），第 6 次近零延迟快速失败。
"""
import asyncio
import threading
import time
from dataclasses import replace

import pytest

from backend.core.tool_runtime import policy as policy_mod
from backend.core.tool_runtime.bulkhead import bulkhead_registry
from backend.core.tool_runtime.circuit_breaker import circuit_registry
from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.models import ToolStatus
from backend.core.tool_runtime.policy import DEFAULT_POLICIES, reset_policy_cache


@pytest.fixture(autouse=True)
def _clean():
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()
    yield
    circuit_registry.reset()
    bulkhead_registry.reset()
    reset_policy_cache()


class _SlowRagTool:
    """可控慢 Tool：逐请求 start/done 事件，用于量化残留线程生命周期。"""

    def __init__(self, seconds: float, n: int):
        self.seconds = seconds
        self.started = [threading.Event() for _ in range(n)]
        self.done = [threading.Event() for _ in range(n)]

    def call(self, i: int):
        def _run():
            self.started[i].set()
            time.sleep(self.seconds)
            self.done[i].set()
            return "late answer"
        return _run


def _patch_policy(monkeypatch, timeout_ms: float, bulkhead_limit: int = 20) -> None:
    monkeypatch.setattr(
        policy_mod, "DEFAULT_POLICIES",
        {"rag.search": replace(
            DEFAULT_POLICIES["rag.search"],
            timeout_ms=timeout_ms, bulkhead_limit=bulkhead_limit,
        )},
    )
    reset_policy_cache()


class TestTimeoutResidualAndConcurrency:
    def test_bulkhead_slot_released_after_timeout_later_call_enters(
        self, monkeypatch,
    ):
        """3 槽全超时后，后续调用仍能进入真实执行（非 TOOL_BUSY）：
        bulkhead 槽位随超时返回立即回收，不被残留线程占死。"""
        _patch_policy(monkeypatch, timeout_ms=500, bulkhead_limit=3)
        tool = _SlowRagTool(seconds=1.5, n=4)

        async def scene():
            # 事件驱动：gather 等待 3 个请求各自真实超时返回（不依赖固定 sleep，
            # 进程首次初始化的收尾抖动不影响判定）
            first_wave = await asyncio.gather(*[
                safe_tool_executor.run(
                    tool_key="rag.search",
                    call=lambda i=i: asyncio.to_thread(tool.call(i)),
                ) for i in range(3)
            ])
            fourth = await safe_tool_executor.run(
                tool_key="rag.search",
                call=lambda: asyncio.to_thread(tool.call(3)),
            )
            return first_wave, fourth

        first_wave, fourth = asyncio.run(scene())
        assert all(r.status is ToolStatus.TIMEOUT for r in first_wave)
        # 第 4 个调用：槽位已回收 → 进入执行 → 同样超时，而非 TOOL_BUSY 快速失败
        assert fourth.status is ToolStatus.TIMEOUT
        assert fourth.error_code != "TOOL_BUSY"
        assert tool.started[3].is_set(), (
            "第 4 个调用应真实进入执行（bulkhead 槽位未被残留线程占死）"
        )
        assert all(d.wait(timeout=5) for d in tool.done)

    def test_residual_thread_outlives_governance_window(self, monkeypatch):
        """治理窗口与残留线程解耦：超时即返回（先于子任务完成），
        to_thread 线程继续在途至子任务自然结束——
        等比映射生产：治理窗口 45s，残留线程最长至 client read 300s。"""
        _patch_policy(monkeypatch, timeout_ms=400)
        tool = _SlowRagTool(seconds=1.2, n=1)
        heavy_done_at = []
        original_call = tool.call

        def timed_call(i):
            _run = original_call(i)

            def _timed():
                _run()
                heavy_done_at.append(time.monotonic())
            return _timed

        async def scene():
            # 预热：进程级一次性初始化（线程池/Prometheus/TraceWriter）
            # 由成功路径先行消化，避免污染正式调用的时序判定
            warm = await safe_tool_executor.run(
                tool_key="rag.search", call=lambda: "warm")
            assert warm.status is ToolStatus.SUCCESS
            result = await safe_tool_executor.run(
                tool_key="rag.search",
                call=lambda: asyncio.to_thread(timed_call(0)),
            )
            return result, time.monotonic()

        result, governance_returned_at = asyncio.run(scene())
        assert result.status is ToolStatus.TIMEOUT
        assert heavy_done_at, "子任务应最终完成"
        assert governance_returned_at < heavy_done_at[0], (
            "治理窗口应先于子任务完成返回（残留线程在途的直接证据）"
        )
        assert tool.done[0].wait(timeout=5), (
            "残留线程应随子任务完成自然结束，不永久泄漏"
        )

    def test_repeated_call_after_timeout_not_blocked(self, monkeypatch):
        """超时后立即重复调用：不被 TOOL_BUSY/熔断拒绝，正常进入执行。
        （此时熔断计数 1 < 阈值 5，重复调用语义与首次一致。）"""
        _patch_policy(monkeypatch, timeout_ms=400)
        tool = _SlowRagTool(seconds=1.0, n=2)

        async def scene():
            first = await safe_tool_executor.run(
                tool_key="rag.search",
                call=lambda: asyncio.to_thread(tool.call(0)),
            )
            second = await safe_tool_executor.run(
                tool_key="rag.search",
                call=lambda: asyncio.to_thread(tool.call(1)),
            )
            return first, second

        first, second = asyncio.run(scene())
        assert first.status is ToolStatus.TIMEOUT
        assert second.status is ToolStatus.TIMEOUT
        assert second.error_code != "TOOL_BUSY"
        assert second.error_code != "CIRCUIT_OPEN"
        assert tool.started[1].is_set()
        assert all(d.wait(timeout=5) for d in tool.done)

    def test_five_timeouts_open_circuit_then_fast_fail(self, monkeypatch):
        """连续 5 次超时 → 熔断 OPEN；第 6 次近零延迟 CIRCUIT_OPEN 快速失败。
        同时验证：快速失败发生在治理层，残留线程仍在后台直至子任务完成。"""
        _patch_policy(monkeypatch, timeout_ms=300)
        tool = _SlowRagTool(seconds=0.8, n=5)

        async def scene():
            waves = []
            for i in range(5):
                waves.append(await safe_tool_executor.run(
                    tool_key="rag.search",
                    call=lambda i=i: asyncio.to_thread(tool.call(i)),
                ))
            t0 = time.monotonic()
            # 熔断已在 executor 入口拦截，call 不会被执行；传占位闭包
            sixth = await safe_tool_executor.run(
                tool_key="rag.search",
                call=lambda: None,
            )
            return waves, sixth, (time.monotonic() - t0) * 1000

        waves, sixth, fast_fail_ms = asyncio.run(scene())
        assert all(r.status is ToolStatus.TIMEOUT for r in waves)
        assert sixth.status is ToolStatus.UNAVAILABLE
        assert sixth.error_code == "CIRCUIT_OPEN"
        assert fast_fail_ms < 50, f"熔断快速失败应近零延迟，实测 {fast_fail_ms:.0f}ms"
        assert all(d.wait(timeout=5) for d in tool.done[:5]), (
            "熔断后 5 个残留线程仍随子任务完成收尾"
        )
