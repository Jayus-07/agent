"""共享线程池 — 检索路径两级分离，避免嵌套提交死锁。

outer: enhanced_hybrid_retrieve 顶层三路并行（rule + dense + sparse）
inner: hybrid / multi_query 内部 vector + BM25 并行
       （被 outer 路径调用时，同线程池会死锁）
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

_outer: ThreadPoolExecutor | None = None
_inner: ThreadPoolExecutor | None = None

_lock = threading.Lock()


def retrieval_pool_outer() -> ThreadPoolExecutor:
    """顶层检索线程池（enhanced 三路并行）。"""
    global _outer
    if _outer is None:
        with _lock:
            if _outer is None:
                _outer = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retr-outer")
    return _outer


def retrieval_pool_inner() -> ThreadPoolExecutor:
    """内层检索线程池（hybrid / multi_query 内部并行）。"""
    global _inner
    if _inner is None:
        with _lock:
            if _inner is None:
                _inner = ThreadPoolExecutor(max_workers=6, thread_name_prefix="retr-inner")
    return _inner
