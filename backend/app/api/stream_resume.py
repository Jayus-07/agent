"""app/api/stream_resume.py — /chat/stream 断线恢复注册表（F2 Resume Protocol）

语义（冻结，详见 docs/2026-09-25-F2-SSE恢复协议-审计与设计.md）：
  - 传输 at-least-once：事件带 (stream_id, seq) 单调序号，重放可能重复；
    前端按 seq 去重 → UI effectively-once。不构造 exactly-once 传输。
  - 断连不中止：客户端断开只脱离订阅，producer 继续执行并把事件写入
    本注册表直到终端帧；重连经 resume(after_seq) 先补历史再切实时。
  - 无丢洞保证：resume 的「取重放尾 + 挂 live 队列」两步在追加锁内
    原子完成——重放与 live 之间不可能插入遗漏。
  - 缓冲有界：超过 SSE_RESUME_BUFFER_MAX_EVENTS 丢最老并置 gap；
    resume 命中 gap → 不可恢复（诚实失败，客户端整轮重发，绝不跳事件）。
  - 进程本地：app 重启即清空 → resume 不可恢复（运行已死，不假恢复）。
  - ping 是连接保活不是流内容：不入注册表、无 seq、不重放。
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

from backend.config.settings import (
    SSE_RESUME_BUFFER_MAX_EVENTS,
    SSE_RESUME_FINISHED_TTL_SECONDS,
)
from backend.shared.logger import logger


@dataclass(eq=False)
class _Waiter:
    """live 订阅者：绑定事件循环 + 其消费队列（eq=False → 身份哈希，可入 set）。

    producer 在 executor 线程投递，asyncio.Queue 非线程安全——必须经
    loop.call_soon_threadsafe（与 chat.py producer→consumer 同一纪律）。
    """

    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


@dataclass
class StreamEvent:
    """带单调序号的流事件（seq 从 1 开始；终端帧也占 seq）。"""

    seq: int
    event: dict


class StreamRecord:
    """单个流（stream_id = request_id）的缓冲与订阅状态。"""

    def __init__(self, *, stream_id: str, session_id: str,
                 user_id: str, tenant_id: str):
        self.stream_id = stream_id
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self._lock = threading.Lock()
        self._buffer: list[StreamEvent] = []
        self._next_seq = 0
        self._truncated = False          # 头部被丢弃（gap）标记
        self._status = "live"            # live | finished
        self._waiters: set[_Waiter] = set()

    # ── producer 侧 ──────────────────────────────────────────
    def append(self, event: dict) -> StreamEvent:
        """producer 线程调用：seq 编号 + 入缓冲 + 投递订阅者。

        原地给 event 注入 seq（producer 事件 dict 为本流私有，无共享）。
        """
        with self._lock:
            self._next_seq += 1
            evt = StreamEvent(seq=self._next_seq, event=event)
            event["seq"] = evt.seq
            self._buffer.append(evt)
            if len(self._buffer) > SSE_RESUME_BUFFER_MAX_EVENTS:
                self._buffer.pop(0)
                self._truncated = True
            for w in tuple(self._waiters):
                try:
                    w.loop.call_soon_threadsafe(w.queue.put_nowait, evt)
                except RuntimeError:
                    self._waiters.discard(w)  # 绑定 loop 已关，静默移除
            return evt

    def finish(self) -> None:
        """producer 终态后调用；晚到订阅仍可重放到终端。"""
        with self._lock:
            self._status = "finished"
            self.finished_at = time.monotonic()
            self._waiters.clear()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    # ── resume 侧 ────────────────────────────────────────────
    def subscribe_after(self, after_seq: int) -> tuple[list[StreamEvent],
                                                       bool, asyncio.Queue]:
        """取 seq>after_seq 的重放尾并原子挂上 live 队列（无丢洞的关键）。

        返回 (replay, gap, queue)：
          replay  — 需要先补发的历史事件（含终端帧，若已结束）
          gap     — True = after_seq 之后存在被丢弃的头部（不可安全续播）
          queue   — live 订阅队列；流已 finished 时不挂队列（不会再来事件）
        必须在锁内同时完成「拷贝尾部」与「注册 waiters」——先拷后注册的
        check-then-act 竞态会丢洞。仅在事件循环内调用（async 端点）。
        """
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._truncated and (not self._buffer
                                    or self._buffer[0].seq > after_seq + 1):
                return [], True, q
            replay = [e for e in self._buffer if e.seq > after_seq]
            if self._status != "finished":
                self._waiters.add(_Waiter(loop=loop, queue=q))
            return replay, False, q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._waiters = {w for w in self._waiters if w.queue is not q}

    def identity_matches(self, *, user_id: str, tenant_id: str) -> bool:
        return self.user_id == user_id and self.tenant_id == tenant_id

    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at


class StreamRegistry:
    """进程内流注册表（单例；app 重启即清空 = 诚实不可恢复）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, StreamRecord] = {}

    def create(self, *, stream_id: str, session_id: str,
               user_id: str, tenant_id: str) -> StreamRecord:
        record = StreamRecord(stream_id=stream_id, session_id=session_id,
                              user_id=user_id, tenant_id=tenant_id)
        with self._lock:
            self._sweep_locked()
            self._records[stream_id] = record
        return record

    def get(self, stream_id: str) -> StreamRecord | None:
        with self._lock:
            self._sweep_locked()
            return self._records.get(stream_id)

    def _sweep_locked(self) -> None:
        """惰性清扫：finished 且超 TTL 的记录移除（晚到 resume 404）。"""
        now = time.monotonic()
        stale = [
            sid for sid, rec in self._records.items()
            if rec.status == "finished" and rec.finished_at is not None
            and now - rec.finished_at > SSE_RESUME_FINISHED_TTL_SECONDS
        ]
        for sid in stale:
            self._records.pop(sid, None)
        if stale:
            logger.debug("[StreamRegistry] swept %d finished streams", len(stale))


_registry: StreamRegistry | None = None
_registry_lock = threading.Lock()


def get_stream_registry() -> StreamRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = StreamRegistry()
    return _registry


def reset_stream_registry() -> None:
    """测试隔离用：清空单例。"""
    global _registry
    with _registry_lock:
        _registry = StreamRegistry()
