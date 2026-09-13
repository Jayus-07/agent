"""MemoryManager — L1+L2+L3 lifecycle orchestrator, sync→async bridge.

Fixes:
  - 后台线程 run_forever() 持续驱动 event loop（旧实现只 run_until_complete
    warmup，返回后 is_running()==False，_run() 全部静默降级，
    会话/消息持久化从未执行）
  - Reduce timeout 120s → 5s (memory is non-critical)
  - Silent no-op when not ready (no warning spam)
"""
import asyncio
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
        # 专用守护线程持有 run_forever() 的 event loop，保证 _run() 随时可提交协程。
        # 不阻塞等待 —— 让 init 异步完成（memory 非关键路径）
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="memory-loop")
        self._thread.start()
        atexit.register(self._shutdown)

    def _run_loop(self) -> None:
        """在后台线程创建 event loop 并持续运行，启动时顺带预热 DB engine。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _startup():
            try:
                from backend.memory.database import _ensure_engine
                await _ensure_engine()
                logger.info("[Memory] 后台 event loop + DB engine 就绪")
            except Exception as e:
                logger.warning(f"[Memory] 预热失败（非致命，首次调用时重试）: {e}")
            finally:
                self._ready.set()

        loop.create_task(_startup())
        loop.run_forever()

    def _run(self, coro_factory):
        """在后台 loop 上执行协程，最多等 _MEMORY_TIMEOUT 秒。

        返回结果或 None（超时/失败时降级）。降级必须留痕（error 日志 +
        degradation_alerts_total），静默丢失上下文是最难排查的故障形态。
        """
        if not self._ready.wait(timeout=_MEMORY_TIMEOUT):
            self._degraded("memory_loop_not_ready", "memory loop 未就绪，本次记忆操作被跳过")
            return None
        loop = self._loop
        if loop is None or not loop.is_running():
            self._degraded("memory_loop_not_running", "memory loop 未运行，本次记忆操作被跳过")
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
            return future.result(timeout=_MEMORY_TIMEOUT)
        except Exception as e:
            self._degraded("memory_op_failed", f"memory 操作失败: {e}")
            return None

    @staticmethod
    def _degraded(code: str, message: str) -> None:
        logger.error(f"[MemoryManager] {message} (code={code})")
        try:
            from backend.observability.metrics import degradation_alerts_total
            degradation_alerts_total.labels(code=code, level="warn").inc()
        except Exception:
            pass

    def _shutdown(self) -> None:
        try:
            loop = self._loop
            if loop is not None and loop.is_running():
                async def _close():
                    from backend.memory.database import _engine
                    if _engine is not None:
                        await _engine.dispose()
                future = asyncio.run_coroutine_threadsafe(_close(), loop)
                future.result(timeout=3)
                loop.call_soon_threadsafe(loop.stop)
        except Exception:
            logger.debug("[P1-10] async engine 关闭失败（进程退出路径）", exc_info=True)

    def start_session(self, session_id: str, question: str, user_id: str = "default") -> ShortTermBuffer:
        # question 透传给 L3：长期记忆检索用当前问题做语义 query（此前误用 session_id）
        result = self._run(lambda: self._service.start_session(session_id, user_id, query=question))
        if result is None:
            return ShortTermBuffer()
        return result

    def end_turn(self, session_id: str, question: str, answer: str, user_id: str = "default") -> None:
        self._run(lambda: self._service.end_turn(session_id, question, answer, user_id))

    @property
    def service(self) -> MemoryService:
        """底层 MemoryService（供记忆工具等需要完整超时/报错的调用方使用）。"""
        return self._service

    def run_tool(self, coro_factory, timeout: float = 15.0):
        """在后台 loop 执行协程并返回结果（供 Tool 层调用）。

        与 _run（内部静默降级，5s）不同：工具是显式调用路径，超时/失败
        必须抛异常，让工具返回错误信息而不是静默空结果。
        """
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError("memory event loop 未就绪")
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("memory event loop 未运行")
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return future.result(timeout=timeout)


memory_manager = MemoryManager()
