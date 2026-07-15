"""MemoryManager — L1+L2+L3 lifecycle orchestrator, backward-compat wrapper"""
import asyncio
import concurrent.futures
import atexit
from backend.memory.service import MemoryService
from backend.memory.short_term import ShortTermBuffer
from backend.shared.logger import logger


class MemoryManager:
    """Sync→async bridge using a persistent background event loop.

    SQLAlchemy async engine pools are event-loop-bound. Using asyncio.run()
    per call destroys the loop each time, corrupting the pool. Instead we
    keep one dedicated thread with one event loop alive for the process
    lifetime.
    """

    def __init__(self):
        self._service = MemoryService()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Warm up: create the loop and keep it alive
        self._ready = False
        self._executor.submit(self._init_loop).result(timeout=10)
        atexit.register(self._shutdown)

    def _init_loop(self) -> None:
        """Create loop → init DB engine ON this loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Force engine creation on THIS background loop
        async def _warmup():
            from backend.memory.database import _ensure_engine
            await _ensure_engine()
        loop.run_until_complete(_warmup())
        self._ready = True

    def _run(self, coro):
        """Run coroutine on the persistent background loop."""
        future = asyncio.run_coroutine_threadsafe(coro, asyncio.get_event_loop())
        return future.result(timeout=120)

    def _shutdown(self) -> None:
        """Clean shutdown — close the engine's connections."""
        try:
            async def _close():
                from backend.memory.database import async_engine
                await async_engine.dispose()
            self._executor.submit(
                lambda: asyncio.get_event_loop().run_until_complete(_close())
            ).result(timeout=5)
        except Exception:
            pass
        self._executor.shutdown(wait=False)

    def start_session(self, session_id: str, question: str) -> ShortTermBuffer:
        try:
            return self._run(self._service.start_session(session_id))
        except Exception as e:
            logger.warning(f"[Memory] start_session 失败（非致命）: {e}")
            return ShortTermBuffer()

    def end_turn(self, session_id: str, question: str, answer: str) -> None:
        try:
            return self._run(self._service.end_turn(session_id, question, answer))
        except Exception as e:
            logger.warning(f"[Memory] end_turn 失败（非致命）: {e}")


memory_manager = MemoryManager()
