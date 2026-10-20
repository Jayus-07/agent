"""run_on_worker_loop 回归：Celery 任务常驻 loop 治理（2026-10-21 连接耗尽事故）。

事故根因：任务用 asyncio.run() 每次新建并关闭 loop，memory 异步引擎随
loop 切换重建，旧引擎的 asyncpg 连接挂在已关闭的 loop 上无法释放，常驻
worker 进程内按任务数累积泄漏（4 小时 47 个，打穿 max_connections）。
"""
import asyncio
import threading

import pytest

from backend.infra import async_utils


@pytest.fixture(autouse=True)
def _reset_worker_loop():
    """每个用例前后清空模块级常驻 loop，避免跨用例污染。"""
    async_utils._worker_loop = None
    yield
    if async_utils._worker_loop is not None and not async_utils._worker_loop.is_closed():
        async_utils._worker_loop.close()
    async_utils._worker_loop = None


def test_worker_loop_reused_across_calls():
    """连续多次调用复用同一个 loop——这是引擎不再重建、连接得以复用的前提。"""
    seen = []

    async def _probe() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    for _ in range(3):
        seen.append(async_utils.run_on_worker_loop(_probe()))

    assert len(set(seen)) == 1, "常驻 loop 必须跨调用复用"
    assert seen[0] is async_utils._worker_loop
    assert not seen[0].is_closed()


def test_worker_loop_keeps_contextvar():
    """协程在调用线程执行：ContextVar（调用方身份/租户上下文）原样可见。"""
    from contextvars import ContextVar

    var: ContextVar[str] = ContextVar("test_ctx")
    var.set("tenant-A")

    async def _read() -> str:
        return var.get()

    assert async_utils.run_on_worker_loop(_read()) == "tenant-A"


def test_worker_loop_serializes_sequential_calls():
    """同一 loop 上顺序执行的多个协程看到彼此的副作用（同 loop 语义）。"""
    state: list[str] = []

    async def _append(name: str) -> None:
        state.append(name)

    async_utils.run_on_worker_loop(_append("a"))
    async_utils.run_on_worker_loop(_append("b"))

    assert state == ["a", "b"]


def test_worker_loop_rebuilt_after_close():
    """loop 被外部关闭后（进程级异常兜底场景）下次调用自动重建。"""
    async def _noop() -> bool:
        return True

    async_utils.run_on_worker_loop(_noop())
    old = async_utils._worker_loop
    old.close()

    async_utils.run_on_worker_loop(_noop())

    assert async_utils._worker_loop is not old
    assert not async_utils._worker_loop.is_closed()


def test_worker_loop_concurrent_first_call_single_loop():
    """首个调用的并发竞争（线程安全）也只建一个 loop。"""
    loops: list[asyncio.AbstractEventLoop] = []
    barrier = threading.Barrier(4)

    async def _probe() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    def _worker():
        barrier.wait()
        loops.append(async_utils.run_on_worker_loop(_probe()))

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 并发首调存在竞争窗口：允许最多重建一次，但不得每线程各建一个
    assert len(set(loops)) <= 2
    assert all(not lp.is_closed() or lp is not async_utils._worker_loop for lp in loops)
