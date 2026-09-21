"""tool_runtime/bulkhead.py — 并发隔离舱（以 tool/service 维度隔离）

避免一个服务挂住后耗尽 HTTP 连接池 / DB pool / asyncio 任务：
每个 tool 一个并发上限（asyncio.Semaphore 语义），满时不无限等待 ——
极短等待（默认 300ms）拿不到槽位即返回 TOOL_BUSY 快速失败走降级。

实现说明：执行链跨多层线程（thread-local event loop），asyncio.Semaphore
绑定 loop 有跨 loop 风险，这里用"计数器 + threading.Lock + 短轮询"实现
等价语义，跨 loop 安全且临界区微秒级。
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass


@dataclass
class BulkheadStats:
    limit: int
    in_use: int


class Bulkhead:
    """单 tool 维度的并发隔离。acquire(wait_ms) 拿不到槽位返回 False（不阻塞不挂起）。"""

    def __init__(self, key: str, limit: int):
        self.key = key
        self.limit = max(1, limit)
        self._in_use = 0
        self._lock = threading.Lock()

    async def acquire(self, wait_ms: float = 300.0) -> bool:
        """尝试占一个槽位；极短轮询等待，超时快速失败。"""
        deadline = time.monotonic() + max(wait_ms, 0) / 1000
        while True:
            with self._lock:
                if self._in_use < self.limit:
                    self._in_use += 1
                    return True
            if time.monotonic() >= deadline:
                return False
            # 10ms 轮询：CPU 开销可忽略（0.5Hz 级），等待语义与 Semaphore 一致
            await asyncio.sleep(0.01)

    def release(self) -> None:
        with self._lock:
            self._in_use = max(0, self._in_use - 1)

    def stats(self) -> BulkheadStats:
        with self._lock:
            return BulkheadStats(limit=self.limit, in_use=self._in_use)


class BulkheadRegistry:
    """tool key → Bulkhead。全局单例；limit 取自该 tool 的策略。"""

    def __init__(self) -> None:
        self._bulkheads: dict[str, Bulkhead] = {}
        self._lock = threading.Lock()

    def get(self, key: str, limit: int = 20) -> Bulkhead:
        with self._lock:
            bh = self._bulkheads.get(key)
            if bh is None:
                bh = Bulkhead(key, limit)
                self._bulkheads[key] = bh
            return bh

    def reset(self, key: str | None = None) -> None:
        """测试钩子。"""
        with self._lock:
            if key is None:
                self._bulkheads.clear()
            else:
                self._bulkheads.pop(key, None)

    def snapshot_all(self) -> dict[str, BulkheadStats]:
        with self._lock:
            return {k: bh.stats() for k, bh in self._bulkheads.items()}


# 全局注册表（进程内单例）
bulkhead_registry = BulkheadRegistry()
