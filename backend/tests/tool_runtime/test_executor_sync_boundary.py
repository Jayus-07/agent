"""tests/tool_runtime/test_executor_sync_boundary.py — SafeToolExecutor 同步边界

2026-10-07 STOP A 回归（12306 全挂事故）：治理同步适配器（execute_sync）
用 asyncio.run 驱动执行器，同步 Tool 此前在 loop 线程内联执行——内部再起
asyncio.run（MCP 同步桥形态）即抛 RuntimeError，12306/知乎真实调用全挂。

契约：
  1. 同步 call 必须不在事件循环线程上执行（to_thread 工作线程）；
  2. 同步 call 内部再起事件循环（asyncio.run）必须成功；
  3. 协程 call 语义不变（直接在当前 loop 上 await）；
  4. ContextVar 随 to_thread 复制（归因/会话上下文不丢）。
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from backend.core.tool_runtime.executor import safe_tool_executor
from backend.core.tool_runtime.models import ToolStatus
from backend.core.tool_runtime.policy import ToolPolicy


def _policy() -> ToolPolicy:
    # 关熔断/隔离舱等待，保持单测确定性（不测熔断语义，那是别的用例）
    return ToolPolicy(circuit_breaker=False, retries=0, bulkhead_wait_ms=0)


def test_sync_call_runs_off_loop_thread() -> None:
    seen: dict[str, int] = {}

    def sync_tool() -> dict:
        seen["worker"] = threading.get_ident()
        return {"ok": True}

    async def scenario() -> object:
        seen["loop"] = threading.get_ident()
        result = await safe_tool_executor.run(
            tool_key="test.sync_boundary", call=sync_tool, policy=_policy(),
        )
        assert result.status is ToolStatus.SUCCESS
        return result

    asyncio.run(scenario())
    assert seen["worker"] != seen["loop"], "同步 Tool 不应在事件循环线程上执行"


def test_sync_tool_with_inner_event_loop_succeeds() -> None:
    """修复前此用例在 execute_sync 场景下必炸（嵌套 asyncio.run）。"""

    def sync_tool() -> dict:
        asyncio.run(asyncio.sleep(0))
        return {"ok": True}

    async def scenario() -> object:
        return await safe_tool_executor.run(
            tool_key="test.sync_nested_loop", call=sync_tool, policy=_policy(),
        )

    result = asyncio.run(scenario())
    assert result.status is ToolStatus.SUCCESS
    assert result.data == {"ok": True}


def test_coroutine_call_still_awaits_on_current_loop() -> None:
    loop_threads: list[int] = []

    async def async_tool() -> dict:
        loop_threads.append(threading.get_ident())
        await asyncio.sleep(0)
        return {"ok": "async"}

    async def scenario() -> object:
        return await safe_tool_executor.run(
            tool_key="test.async_call", call=async_tool, policy=_policy(),
        )

    result = asyncio.run(scenario())
    assert result.status is ToolStatus.SUCCESS
    assert result.data == {"ok": "async"}
    # 协程本体仍在当前 loop 线程执行（不被挪去线程池）
    loop_thread = threading.get_ident()
    assert loop_threads and all(t == loop_thread for t in loop_threads)


def test_contextvars_propagate_into_sync_thread() -> None:
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar(
        "test_marker", default="")
    observed: dict[str, str] = {}

    def sync_tool() -> dict:
        observed["value"] = marker.get()
        return {"ok": True}

    async def scenario() -> object:
        token = marker.set("ctx-42")
        try:
            return await safe_tool_executor.run(
                tool_key="test.ctx_propagation", call=sync_tool,
                policy=_policy(),
            )
        finally:
            marker.reset(token)

    asyncio.run(scenario())
    assert observed["value"] == "ctx-42"


def test_sync_tool_exception_maps_to_tool_result() -> None:
    def broken() -> dict:
        raise ConnectionError("connection refused")

    async def scenario() -> object:
        return await safe_tool_executor.run(
            tool_key="test.sync_failure", call=broken, policy=_policy(),
        )

    result = asyncio.run(scenario())
    assert result.status is ToolStatus.UNAVAILABLE
    assert result.error_code == "connect_error"
    assert result.retryable is True
