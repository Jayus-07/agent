"""MemoryManager — L1+L2+L3 lifecycle orchestrator, backward-compat wrapper"""
import asyncio
import concurrent.futures
import atexit
from memory.service import MemoryService
from memory.short_term import ShortTermBuffer
from utils.logger import logger


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
        """Create a persistent event loop in this thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ready = True
        # This thread stays alive serving future _run() calls

    def _run(self, coro):
        """Run async coroutine on the persistent loop, then drain pending tasks."""
        def _execute():
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(coro)
            # Give pending background tasks (e.g. L3 store) a chance to run
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            return result
        future = self._executor.submit(_execute)
        return future.result(timeout=120)

    def _shutdown(self) -> None:
        """Clean shutdown — close the engine's connections."""
        try:
            async def _close():
                from memory.database import async_engine
                await async_engine.dispose()
            self._executor.submit(
                lambda: asyncio.get_event_loop().run_until_complete(_close())
            ).result(timeout=5)
        except Exception:
            pass
        self._executor.shutdown(wait=False)

    def start_session(self, session_id: str, question: str) -> ShortTermBuffer:
        return self._run(self._service.start_session(session_id))

    def end_turn(self, session_id: str, question: str, answer: str) -> None:
        return self._run(self._service.end_turn(session_id, question, answer))

    def end_session(self, session_id: str) -> None:
        if session_id in self._service._sessions:
            del self._service._sessions[session_id]


memory_manager = MemoryManager()
