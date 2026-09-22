"""tasks/lease_heartbeat.py — 执行租约心跳线程（Phase2 Step1）。

职责：Worker 执行任务期间周期性续租（renew_lease），覆盖 rag_index 这类
长原子节点内部——节点执行中无法经过 executor 的节点边界，只有心跳线程
能维持租约活性。

语义：
- 续租成功：lease_heartbeat_at / lease_expires_at 前移（stale 判定唯一权威）
- 续租失败（rowcount=0）：租约已被接管/过期 → ``lost`` 置位，停止续租。
  executor 在节点边界检查 ``lost``，fencing 写亦会同步拒绝——旧 Worker
  停止写 TaskState/checkpoint/event 并退出。
- 续租抛异常（DB 抖动）：记录后继续下一轮；若 DB 持续不可用，executor
  的 fencing 写会以显式异常失败，不会静默双写。

线程形态：daemon 线程，随宿主进程退出；prefork 子进程内每任务一个实例。
"""
from __future__ import annotations

import threading

from backend.models.task import TaskLeaseLost
from backend.shared.logger import logger


class LeaseHeartbeat:
    """单次执行租约的心跳维持器（支持 with 语法）。"""

    def __init__(self, task_id: str, execution_id: str, *,
                 interval_s: float | None = None,
                 ttl_s: int | None = None):
        from backend.config.tasks import (TASK_LEASE_HEARTBEAT_INTERVAL,
                                          TASK_LEASE_TTL_SECONDS)

        self.task_id = task_id
        self.execution_id = execution_id
        self._interval = float(
            interval_s if interval_s is not None
            else TASK_LEASE_HEARTBEAT_INTERVAL)
        self._ttl = int(ttl_s if ttl_s is not None else TASK_LEASE_TTL_SECONDS)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"lease-hb-{task_id[:8]}-{execution_id[:8]}")

    # ── 生命周期 ─────────────────────────────────────────
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """停止心跳（终态落库后 / 退出前调用；幂等）。"""
        self._stop.set()

    def __enter__(self) -> "LeaseHeartbeat":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # ── 状态 ─────────────────────────────────────────────
    @property
    def lost(self) -> bool:
        """租约是否已被接管/过期（置位后不再恢复）。"""
        return self._lost.is_set()

    def raise_if_lost(self) -> None:
        """节点边界守卫：租约丢失立即抛 TaskLeaseLost 终止执行。"""
        if self._lost.is_set():
            raise TaskLeaseLost(self.task_id, self.execution_id)

    # ── 心跳循环 ─────────────────────────────────────────
    def _loop(self) -> None:
        from backend.services import task_service

        while not self._stop.wait(self._interval):
            try:
                ok = task_service.renew_lease(
                    self.task_id, self.execution_id, lease_ttl_seconds=self._ttl)
            except Exception:  # noqa: BLE001 — DB 抖动不判丢失，下轮重试
                logger.warning("[LeaseHeartbeat] %s 续租 DB 异常，下轮重试",
                               self.task_id, exc_info=True)
                continue
            if not ok:
                self._lost.set()
                logger.warning(
                    "[LeaseHeartbeat] %s 租约丢失（execution=%s 已被接管或"
                    "过期），停止续租；后续写入将被 fencing 拒绝",
                    self.task_id, self.execution_id[:8])
                return
