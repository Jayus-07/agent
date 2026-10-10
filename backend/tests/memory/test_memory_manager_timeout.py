"""MemoryManager 桥接超时后应取消仍在运行的请求协程。"""

import asyncio
import threading

from backend.memory.manager import MemoryManager


def test_timeout_cancels_coroutine_instead_of_leaving_background_work(
    monkeypatch,
):
    loop = asyncio.new_event_loop()
    loop_started = threading.Event()
    coroutine_started = threading.Event()
    coroutine_cancelled = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop_started.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    assert loop_started.wait(timeout=1)
    monkeypatch.setattr("backend.memory.manager._MEMORY_TIMEOUT", 0.02)
    monkeypatch.setattr(MemoryManager, "_degraded", staticmethod(lambda *_: None))

    submit = asyncio.run_coroutine_threadsafe

    def submit_after_start(coro, target_loop):
        future = submit(coro, target_loop)
        assert coroutine_started.wait(timeout=1)
        return future

    monkeypatch.setattr(
        "backend.memory.manager.asyncio.run_coroutine_threadsafe",
        submit_after_start,
    )

    manager = MemoryManager.__new__(MemoryManager)
    manager._ready = threading.Event()
    manager._ready.set()
    manager._loop = loop

    async def slow_operation():
        coroutine_started.set()
        try:
            await asyncio.sleep(60)
        finally:
            coroutine_cancelled.set()

    async def cancel_pending_tasks():
        current = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        assert manager._run(slow_operation) is None
        assert coroutine_started.wait(timeout=1)
        assert coroutine_cancelled.wait(timeout=1)
    finally:
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(
                cancel_pending_tasks(), loop,
            ).result(timeout=1)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=1)
        loop.close()
