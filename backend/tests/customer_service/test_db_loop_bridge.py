"""customer_service/_db_loop.run_sync 桥语义回归（cs-v2 审查 R1 修复）。

背景（docs/tasks/cs-workstation-v2/REVIEW.md §4.2 R1）：幂等回调内的落库
桥超时后，在途协程的行为决定「同键重试是否重复插入」。本文件锁定桥的三条
语义：结果透传、默认超时仅放弃等待（协程继续执行，既有调用方依赖）、
cancel_on_timeout 传播取消（协程在 await 点收到 CancelledError，不再完成
副作用）。
"""
import asyncio
import threading

import pytest
from concurrent.futures import TimeoutError as FutureTimeoutError

from backend.customer_service import _db_loop


def test_run_sync_returns_result():
    async def echo():
        return 42

    assert _db_loop.run_sync(echo()) == 42


def test_run_sync_timeout_default_keeps_coroutine_running():
    """默认语义（不取消）：超时仅放弃等待，协程继续执行至完成。"""
    done = threading.Event()

    async def slow():
        await asyncio.sleep(0.6)
        done.set()
        return "ok"

    with pytest.raises(FutureTimeoutError):
        _db_loop.run_sync(slow(), timeout=0.05)
    assert done.wait(5.0), "默认 cancel_on_timeout=False 下协程应继续执行至完成"


def test_run_sync_timeout_with_cancel_aborts_coroutine():
    """cancel_on_timeout=True：超时后取消在途协程，副作用不再完成。"""
    started = threading.Event()
    cancelled = threading.Event()

    async def slow():
        started.set()
        try:
            await asyncio.sleep(5)
            return "never"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(FutureTimeoutError):
        _db_loop.run_sync(slow(), timeout=0.05, cancel_on_timeout=True)
    assert started.wait(5.0)
    assert cancelled.wait(5.0), "在途协程应收到 CancelledError（Session begin() 随即回滚）"
