"""MemoryManager — L1+L2+L3 lifecycle orchestrator, sync→async bridge.

Fixes (P0 perf):
  - Store background loop reference, don't rely on MainThread event loop
  - Reduce timeout 120s → 5s (memory is non-critical)
  - Silent no-op when not ready (no warning spam)
"""
import asyncio
import concurrent.futures
import atexit
import threading
from backend.memory.service import MemoryService
from backend.memory.short_term import ShortTermBuffer
from backend.shared.logger import logger

_MEMORY_TIMEOUT = 5  # 非关键路径，5s 够用


class MemoryManager:
    """Sync→async bridge — 独立后台线程持有 event loop+DB engine。

    LangGraph sync invoke() 在 MainThread 无 event loop。
    本类在后台线程创建 event loop，所有 async 操作提交到该线程执行。
    """

    def __init__(self):
        self._service = MemoryService()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._executor.submit(self._init_loop)
        # 不阻塞等待 —— 让 init 异步完成（memory 非关键路径）
        atexit.register(self._shutdown)

    def _init_loop(self) -> None:
        """在后台线程创建 event loop + 初始化 DB engine。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            async def _warmup():
                from backend.memory.database import _ensure_engine
                await _ensure_engine()
            loop.run_until_complete(_warmup())
            self._ready.set()
            logger.info("[Memory] 后台 event loop + DB engine 就绪")
        except Exception as e:
            logger.warning(f"[Memory] 初始化失败（非致命，功能降级）: {e}")

    def _run(self, coro_factory):
        """在后台 loop 上执行协程，最多等 _MEMORY_TIMEOUT 秒。

        返回结果或 None（超时/失败时静默降级）。
        """
        if self._loop is None or not self._loop.is_running():
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
            return future.result(timeout=_MEMORY_TIMEOUT)
        except (concurrent.futures.TimeoutError, RuntimeError, Exception):
            return None

    def _shutdown(self) -> None:
        try:
            if self._loop is not None and self._loop.is_running():
                async def _close():
                    from backend.memory.database import async_engine
                    await async_engine.dispose()
                future = asyncio.run_coroutine_threadsafe(_close(), self._loop)
                future.result(timeout=3)
        except Exception:
            logger.debug("[P1-10] async engine 关闭失败（进程退出路径）", exc_info=True)
        self._executor.shutdown(wait=False)

    def start_session(self, session_id: str, question: str) -> ShortTermBuffer:
        result = self._run(lambda: self._service.start_session(session_id))
        if result is None:
            return ShortTermBuffer()
        return result

    def end_turn(self, session_id: str, question: str, answer: str) -> None:
        self._run(lambda: self._service.end_turn(session_id, question, answer))


memory_manager = MemoryManager()
