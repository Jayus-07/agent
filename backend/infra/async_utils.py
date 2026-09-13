"""
异步工具模块 - 异步调用包装器（带超时保护）+ 安全运行异步协程
"""
import asyncio
import concurrent.futures
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
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=timeout,
            )
        return result
    except asyncio.TimeoutError:
        logger.warning(f"⚠️ {error_message}")
        return default_value
    except Exception as e:
        logger.error(f"❌ 异步调用失败: {e}")
        return default_value