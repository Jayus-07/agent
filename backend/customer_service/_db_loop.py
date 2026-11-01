"""customer_service/_db_loop.py — Sync→Async 桥接

Graph 节点是 sync 函数，DB 基础设施是 async。
本模块在后台 daemon 线程运行一个专属 event loop，
提供 run_sync() 让 sync 代码调用 async 协程。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from concurrent.futures import ThreadPoolExecutor

_loop = asyncio.new_event_loop()
_loop_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cs-db")
_loop.set_default_executor(_loop_executor)


def _run_loop() -> None:
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


_thread = threading.Thread(target=_run_loop, daemon=True, name="cs-db-loop")
_thread.start()


def run_sync(coro, timeout: float = 10, *, cancel_on_timeout: bool = False):
    """从 sync 上下文提交协程到后台 loop 并阻塞等待结果。

    cancel_on_timeout：超时后向在途协程传播取消（run_coroutine_threadsafe
    返回的 concurrent future 经 _chain_future 把 cancel 转发到本 loop，
    AsyncSession 的 begin() 上下文随即回滚）。供幂等回调等「超时后协程不得
    再完成副作用」的调用方使用；默认 False 保持既有语义——超时仅放弃等待，
    协程继续执行至完成（图节点等调用方依赖该行为，不可全局翻转）。
    """
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        if cancel_on_timeout:
            future.cancel()
        raise


def get_loop() -> asyncio.AbstractEventLoop:
    """后台 loop 引用（maintenance 广播跳板用，勿对其 run_until_complete）。"""
    return _loop
