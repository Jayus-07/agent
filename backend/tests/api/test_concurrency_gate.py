"""优先级并发门（_PriorityGate）单测。

覆盖：即时获取、优先级抢占（high 先于 normal 获得槽位）、排队超时拒绝、
释放归还、竞态兜底（超时瞬间被授予则归还槽位，防泄漏）。
"""
import asyncio

import pytest

from backend.app.api.middleware.concurrency import _PriorityGate, _priority_of


@pytest.fixture
def gate():
    return _PriorityGate(max_concurrent=1)


def test_priority_of_paths():
    assert _priority_of("/chat/stream") == "high"
    assert _priority_of("/chat") == "high"
    assert _priority_of("/cs/handoff") == "high"
    assert _priority_of("/rag/upload") == "normal"
    assert _priority_of("/data/export") == "normal"


def test_immediate_acquire_when_slot_free(gate):
    async def run():
        assert await gate.acquire("high", timeout=1.0) is True

    asyncio.run(run())


def test_high_priority_jumps_queue(gate):
    """normal 先排队、high 后到：槽位释放时 high 先被唤醒拿到槽位。"""
    events = []

    async def run():
        assert await gate.acquire("normal", timeout=1.0) is True  # 占满唯一槽位
        normal_task = asyncio.ensure_future(gate.acquire("normal", timeout=5.0))
        await asyncio.sleep(0.02)
        high_task = asyncio.ensure_future(gate.acquire("high", timeout=5.0))
        await asyncio.sleep(0.05)  # 两者都进入等待队列
        await gate.release()       # 唤醒堆顶 → high
        await asyncio.wait_for(high_task, 1.0)
        events.append("high")
        await gate.release()       # high 再释放 → normal 接手
        await asyncio.wait_for(normal_task, 1.0)
        events.append("normal")

    asyncio.run(run())
    assert events == ["high", "normal"]


def test_normal_gets_slot_after_high_releases():
    """high 拿到槽位再释放时，排队的 normal 应接手。"""
    gate = _PriorityGate(max_concurrent=1)

    async def run():
        assert await gate.acquire("high", timeout=1.0) is True
        normal_task = asyncio.ensure_future(gate.acquire("normal", timeout=5.0))
        await asyncio.sleep(0.05)
        await gate.release()
        assert await asyncio.wait_for(normal_task, timeout=1.0) is True
        await gate.release()

    asyncio.run(run())


def test_queue_timeout_rejects(gate):
    async def run():
        assert await gate.acquire("high", timeout=1.0) is True
        # 槽位被占，无人 release → 0.05s 后超时拒绝
        assert await gate.acquire("normal", timeout=0.05) is False

    asyncio.run(run())


def test_release_without_waiters_decrements(gate):
    async def run():
        assert await gate.acquire("high", timeout=1.0) is True
        await gate.release()
        # 释放后应能再次立即获取（active 已归零）
        assert await gate.acquire("high", timeout=1.0) is True

    asyncio.run(run())


def test_no_slot_leak_when_timeout_races_grant():
    """竞态兜底：wait_for 超时瞬间 fut 已被 release 授予 → 归还槽位，不泄漏。"""
    gate = _PriorityGate(max_concurrent=1)

    async def run():
        assert await gate.acquire("high", timeout=1.0) is True  # 占满唯一槽位
        # 手工构造"已被授予但调用方按超时处理"的现场：
        # 等待者 fut 已被 set_result(True)，随后 acquire 走超时兜底分支
        fut = asyncio.get_running_loop().create_future()
        import heapq

        heapq.heappush(gate._waiters, (0, 999, fut))
        fut.set_result(True)
        assert await gate._release_if_granted(fut) is True   # 已授予 → 归还

        # 归还后槽位可用（未泄漏）
        assert await gate.acquire("normal", timeout=1.0) is True
        await gate.release()

    asyncio.run(run())
