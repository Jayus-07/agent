"""customer_service/_db_loop.py — Sync→Async 桥接

Graph 节点是 sync 函数，DB 基础设施是 async。
本模块在后台 daemon 线程运行一个专属 event loop，
提供 run_sync() 让 sync 代码调用 async 协程。
"""
from __future__ import annotations

import asyncio
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


def run_sync(coro):
    """从 sync 上下文提交协程到后台 loop 并阻塞等待结果。"""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=10)
