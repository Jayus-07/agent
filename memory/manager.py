"""MemoryManager — L1+L2+L3 lifecycle orchestrator, backward-compat wrapper"""
import asyncio
from memory.service import MemoryService
from memory.short_term import ShortTermBuffer
from utils.logger import logger


class MemoryManager:
    def __init__(self):
        self._service = MemoryService()
        self._loop = None

    def _run(self, coro):
        """Sync wrapper for async calls"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return asyncio.run(coro)

    def start_session(self, session_id: str, question: str) -> ShortTermBuffer:
        return self._run(self._service.start_session(session_id))

    def end_turn(self, session_id: str, question: str, answer: str) -> None:
        return self._run(self._service.end_turn(session_id, question, answer))

    def end_session(self, session_id: str) -> None:
        if session_id in self._service._sessions:
            del self._service._sessions[session_id]


memory_manager = MemoryManager()
