"""共享线程池 — 检索路径三级分离，杜绝嵌套提交死锁（Phase 3 / P0）。

为什么三级（2026-10-07 施工前审计实锤）：

- **multi_query**（专池）：MultiQuery 变体 fan-out 的**父任务**。历史上与
  inner 共池——父任务占坑等待的子任务（hybrid 的 vector/BM25 leg）排在自己
  身后，2 个并发 MQ 请求 × 3 变体 = 6 父占满 inner(6)，子任务永不调度 =
  经典嵌套池线程饥饿死锁。父子绝不共池是硬约束。
- **outer**：enhanced_hybrid_retrieve 顶层三路并行（rule + dense + sparse）。
  leg 内部不再 submit，故 outer 无自嵌套；但 inner 父任务会阻塞等 outer，
  两池独立保证不互相占坑。
- **inner**：hybrid 内部 vector + BM25 并行（被 outer/MQ/请求线程调用时，
  同池自嵌套会死锁）。

所有提交统一走 :func:`submit_rag_task`——自动埋 wait/exec 时长与队列深度
（spec §20：rag.pool.{multi_query|outer|inner}.wait_ms）。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from backend.config.rag import (
    RAG_POOL_INNER_WORKERS,
    RAG_POOL_MULTI_QUERY_WORKERS,
    RAG_POOL_OUTER_WORKERS,
)

_multi_query: ThreadPoolExecutor | None = None
_outer: ThreadPoolExecutor | None = None
_inner: ThreadPoolExecutor | None = None

_lock = threading.Lock()


def retrieval_pool_multi_query() -> ThreadPoolExecutor:
    """MultiQuery 变体 fan-out 专池（父任务）——禁止子任务回流本池。"""
    global _multi_query
    if _multi_query is None:
        with _lock:
            if _multi_query is None:
                _multi_query = ThreadPoolExecutor(
                    max_workers=RAG_POOL_MULTI_QUERY_WORKERS,
                    thread_name_prefix="retr-mq",
                )
    return _multi_query


def retrieval_pool_outer() -> ThreadPoolExecutor:
    """顶层检索线程池（enhanced 三路并行）。"""
    global _outer
    if _outer is None:
        with _lock:
            if _outer is None:
                _outer = ThreadPoolExecutor(
                    max_workers=RAG_POOL_OUTER_WORKERS,
                    thread_name_prefix="retr-outer",
                )
    return _outer


def retrieval_pool_inner() -> ThreadPoolExecutor:
    """内层检索线程池（hybrid 内部 vector/BM25 并行）。"""
    global _inner
    if _inner is None:
        with _lock:
            if _inner is None:
                _inner = ThreadPoolExecutor(
                    max_workers=RAG_POOL_INNER_WORKERS,
                    thread_name_prefix="retr-inner",
                )
    return _inner


_POOL_FACTORIES: dict[str, Callable[[], ThreadPoolExecutor]] = {
    "multi_query": retrieval_pool_multi_query,
    "outer": retrieval_pool_outer,
    "inner": retrieval_pool_inner,
}


def _queued_tasks(pool: ThreadPoolExecutor) -> int | None:
    """提交时刻的队列深度（CPython 私有结构，取不到不埋点）。"""
    try:
        return pool._work_queue.qsize()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — 观测旁路
        return None


def submit_rag_task(pool_name: str, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
    """向指定检索池提交任务，自动埋 wait/exec 时长与队列深度（spec §20）。

    - wait_ms：submit → 任务开始执行的排队时长（池饥饿的直接信号）
    - exec_ms：任务自身执行时长
    - rag_pool_queued_tasks：提交时刻队列深度（gauge 采样）

    注意：contextvars 快照语义由调用方负责（如 multi_query 在提交方线程
    copy_context 后传 ctx.run 进来），本包装不碰 context。
    """
    factory = _POOL_FACTORIES.get(pool_name)
    if factory is None:
        raise ValueError(f"未知检索池: {pool_name}（合法: {sorted(_POOL_FACTORIES)}）")
    pool = factory()
    submitted_at = time.perf_counter()

    def _runner() -> Any:
        started_at = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            try:
                from backend.observability.metrics import record_rag_pool_timing

                record_rag_pool_timing(
                    pool_name,
                    wait_ms=(started_at - submitted_at) * 1000,
                    exec_ms=(time.perf_counter() - started_at) * 1000,
                    queued=_queued_tasks(pool),
                )
            except Exception:  # noqa: BLE001 — 观测旁路软失败
                pass

    return pool.submit(_runner)
