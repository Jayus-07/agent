"""middleware/concurrency.py — CPU 保护并发控制中间件（优先级排队版）

限制同时处理的请求数，防止 CPU 过载关机。

设计（2026-09-13 优先级排队改造）：
  - 轻量只读端点（RAG 查询、统计、operations）跳过信号量，不受并发限制
  - 重量端点（LLM 聊天、上传、重索引）受并发门（_PriorityGate）保护
  - 槽位满时不再一律 503：交互式请求（对话/客服）进高优先级等待队列，
    槽位释放时优先唤醒等待最久的高优先级请求；等待超过
    CONCURRENCY_QUEUE_TIMEOUT 秒仍拿不到槽位才拒绝（503 + Retry-After）
  - 上传/重索引等批量请求为 normal 优先级：高优先级等待者存在时，
    槽位永远先给交互式请求，避免批量任务饿死用户对话
"""
import asyncio
import heapq
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import CONCURRENCY_QUEUE_TIMEOUT, MAX_CONCURRENT_REQUESTS
from backend.observability.metrics import (
    request_concurrency_active,
    request_concurrency_queued,
    request_concurrency_reject_total,
    request_concurrency_wait_seconds,
)

# 交互式端点前缀：占满时插队（高优先级），保证对话 P99 TTFT 不被批量任务拖垮
_HIGH_PRIORITY_PREFIXES = (
    "/chat/stream",
    "/chat",
    "/cs",       # 客服对话图
)

# 不阻塞的路径前缀（轻量只读 + 系统端点）
_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/rag/operations",   # 操作审计日志（SQLite 只读）
    "/rag/documents",     # 文档列表/详情（SQLite 只读）
    "/rag/stats",         # 统计（SQLite 只读）
    "/rag/chunks",        # Chunk 详情（SQLite 只读）
    "/rag/search",        # 关键词搜索（只读）
    "/rag/knowledge-bases",  # 知识库列表
    "/rag/health",        # 健康检查
    "/rag/memory",        # 记忆查询
    "/memory",            # 记忆模块（只读查询）
    "/data",              # 数据中心（列表查询）
    "/mcp",               # MCP 服务器列表
    "/reports",           # 报告列表（只读）
    "/schedules",         # 定时任务列表
    "/chat/messages",     # 聊天历史（只读查询）
    "/chat/abort",        # 中止请求（控制信号，需立即处理）
    "/prompts",           # 提示词管理（SQLite/PG 只读查询）
    "/evaluation",        # 评测管理（只读查询 + 历史报告）
)


def _priority_of(path: str) -> str:
    """按路径判定优先级：交互式对话 high，其余批量/管理操作 normal。"""
    return "high" if path.startswith(_HIGH_PRIORITY_PREFIXES) else "normal"


class _PriorityGate:
    """带优先级等待队列的并发门。

    acquire(priority, timeout):
      - 有空闲槽位且无等待者 → 立即占用（等待者优先，防插队饥饿）
      - 无槽位 → 进入最小堆等待队列（priority 越小越先唤醒，同级 FIFO）
      - 超时未获得 → 返回 False（调用方 503）
    release():
      - 优先把槽位转交给堆顶等待者（active 计数不变）
      - 无等待者时才真正释放槽位
    """

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._active = 0
        self._seq = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._cond = asyncio.Condition()

    async def acquire(self, priority: str, timeout: float) -> bool:
        # priority 序：数值越小越先出堆 → high 优先于 normal，同优先级 FIFO
        priority_rank = 0 if priority == "high" else 1
        started = time.monotonic()
        async with self._cond:
            if self._active < self._max and not self._waiters:
                self._active += 1
                request_concurrency_active.set(self._active)
                request_concurrency_wait_seconds.labels(
                    priority=priority
                ).observe(time.monotonic() - started)
                return True
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._seq += 1
            heapq.heappush(self._waiters, (priority_rank, self._seq, fut))
            request_concurrency_queued.set(len(self._waiters))
        try:
            await asyncio.wait_for(fut, timeout)
            request_concurrency_wait_seconds.labels(priority=priority).observe(
                time.monotonic() - started
            )
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # 竞态兜底：等待期间恰好被 release 授予槽位 → 归还，不让槽位泄漏
            await self._release_if_granted(fut)
            return False
        finally:
            request_concurrency_queued.set(len(self._waiters))

    async def _release_if_granted(self, fut: asyncio.Future) -> bool:
        """超时/取消后的竞态兜底：fut 恰好已被授予槽位则归还。

        Returns:
            True 表示 fut 在失败前已被授予（槽位已归还，调用方需按未获得处理）。
        """
        if fut.done() and not fut.cancelled() and fut.result():
            await self.release()
            return True
        return False

    async def release(self) -> None:
        async with self._cond:
            while self._waiters:
                _, _, fut = heapq.heappop(self._waiters)
                if not fut.done():
                    # 槽位直接转交等待者，active 计数不变
                    fut.set_result(True)
                    return
            self._active -= 1
            request_concurrency_active.set(self._active)


_gate = _PriorityGate(MAX_CONCURRENT_REQUESTS)


async def concurrency_limit_middleware(request: Request, call_next):
    """限制同时处理的请求数，防止 CPU 过载关机。

    轻量只读端点直接放行；重量端点经优先级并发门，
    排队超时才返回 503。
    """
    path = request.url.path

    # 轻量只读端点：直接放行，不消耗并发槽位
    if path in _SKIP_PREFIXES or any(
        path.startswith(prefix) for prefix in _SKIP_PREFIXES
    ):
        return await call_next(request)

    # 重量端点：进入优先级并发门；CONCURRENCY_QUEUE_TIMEOUT=0 时行为退化为原版（满即 503）
    priority = _priority_of(path)
    granted = await _gate.acquire(priority, CONCURRENCY_QUEUE_TIMEOUT)
    if not granted:
        request_concurrency_reject_total.labels(reason="queue_timeout").inc()
        return JSONResponse(
            status_code=503,
            content={
                "error": "ServerBusy",
                "detail": (
                    f"服务器繁忙，排队 {CONCURRENCY_QUEUE_TIMEOUT:.0f}s 未获得处理槽位"
                    f"（最大并发: {MAX_CONCURRENT_REQUESTS}），请稍后重试"
                ),
            },
            headers={"Retry-After": "5"},
        )

    try:
        return await call_next(request)
    finally:
        await _gate.release()
