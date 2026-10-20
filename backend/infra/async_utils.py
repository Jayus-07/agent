"""
异步工具模块 - 异步调用包装器（带超时保护）+ 安全运行异步协程
"""
import asyncio
import concurrent.futures
import contextvars
import threading
from typing import Callable, Any
from backend.shared.logger import logger

# 同步限时调用的共享线程池；超时后孤儿线程由 provider 构造期超时（默认 30s）
# 自然退出兜底，不会无限堆积
_sync_timeout_pool: concurrent.futures.ThreadPoolExecutor | None = None
_sync_timeout_pool_lock = threading.Lock()


def sync_call_with_timeout(func: Callable, timeout: float, *args, **kwargs) -> Any:
    """在线程中执行同步调用并限时；超时抛 concurrent.futures.TimeoutError。

    背景：LLM invoke 的 config={"timeout"} 在当前 ChatOpenAI 版本实测不生效
    （2026-09 验证），per-call 限时必须走线程级超时。
    """
    global _sync_timeout_pool
    if _sync_timeout_pool is None:
        with _sync_timeout_pool_lock:
            if _sync_timeout_pool is None:
                _sync_timeout_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="sync-timeout",
                )
    future = _sync_timeout_pool.submit(func, *args, **kwargs)
    return future.result(timeout=timeout)


def run_async(coro):
    """安全运行异步协程 — 兼容有/无事件循环两种场景。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_loop_lock = threading.Lock()


def run_on_worker_loop(coro):
    """在当前线程的常驻事件循环上同步执行协程（Celery worker 任务专用）。

    为什么不能用 asyncio.run()：它每次调用新建并关闭一个 loop，绑定
    loop 的资源随 loop 作废——memory 异步引擎（backend/memory/database.py）
    检测到 loop 切换就重建引擎，旧引擎的 asyncpg 连接挂在已关闭的旧
    loop 上无法释放，在常驻 worker 进程内随任务数累积成 PG 连接泄漏
    （2026-10-21 实测：maintenance-worker 4 小时泄漏 47 个连接，打穿
    max_connections 导致全站数据库 500）。常驻 loop 让引擎生命周期与
    worker 进程对齐，连接池跨任务复用。

    约束：Celery prefork 池中任务由子进程主线程串行执行，同一 loop
    不会并发进入本函数；不要把本函数用于多线程并发提交的场景
    （run_until_complete 非线程安全）。协程在当前线程执行，ContextVar
    （调用方身份/租户上下文）原样保留。
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        with _worker_loop_lock:
            if _worker_loop is None or _worker_loop.is_closed():
                _worker_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


async def async_safe_call_with_timeout(
    func: Callable,
    timeout: int,
    default_value: Any = None,
    error_message: str = "操作超时",
    *args,
    **kwargs,
) -> Any:
    """异步安全调用（带超时保护）。同步函数在线程池中执行。"""
    try:
        if asyncio.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
        else:
            # 同步函数 - 在线程池中执行
            loop = asyncio.get_event_loop()
            context = contextvars.copy_context()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: context.run(func, *args, **kwargs)
                ),
                timeout=timeout,
            )
        return result
    except asyncio.TimeoutError:
        logger.warning(f"⚠️ {error_message}")
        return default_value
    except Exception as e:
        logger.error(f"❌ 异步调用失败: {e}")
        return default_value
