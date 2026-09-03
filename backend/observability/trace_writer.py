"""异步 Trace 写入队列 — Redis Streams 优先，本地 queue 降级。

TraceCollector.finish() 的数据质量处理（leaked span 关闭、status 聚合等）
仍在调用线程同步完成；此模块只接管后续的持久化步骤（Langfuse + SQLite +
Analytics），将阻塞 I/O 从请求路径移入后台 worker。

Redis 可用时：XADD → stream `agent:trace:write`，worker XREAD 批量消费。
Redis 不可用时：queue.Queue 本地 fallback，worker get() 逐条消费。
两者都不可用时：同步写入（最差情况，退化为旧行为）。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict
from typing import Any

from backend.shared.logger import logger

_STREAM_KEY = "trace:write"
_BATCH_SIZE = 50
_FLUSH_INTERVAL_S = 2.0


def _serialize_record(record: Any) -> dict:
    """TraceRecord → JSON-safe dict（跳过 _ 前缀内部属性）。"""
    if hasattr(record, "__dict__"):
        d = {}
        for k, v in record.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                d[k] = {dk: dv for dk, dv in v.items()
                        if not (isinstance(dk, str) and dk.startswith("_"))}
            elif isinstance(v, list):
                d[k] = [_serialize_record(x) if hasattr(x, "__dict__") else x
                        for x in v]
            elif hasattr(v, "__dict__"):
                d[k] = _serialize_record(v)
            else:
                d[k] = v
        return d
    return record


class TraceWriteQueue:
    """后台 trace 持久化队列单例。"""

    def __init__(self):
        self._local_queue: queue.Queue[dict | None] = queue.Queue(maxsize=10000)
        self._use_redis = False
        self._redis = None
        self._worker: threading.Thread | None = None
        self._stopped = False
        self._try_init_redis()
        self._start_worker()

    def _try_init_redis(self):
        try:
            from backend.infra.redis.client import get_redis
            r = get_redis()
            if r is not None:
                r.ping()
                self._redis = r
                self._use_redis = True
                logger.info("[TraceWriter] Redis 可用，使用 Streams 异步写入")
            else:
                logger.info("[TraceWriter] Redis 不可用，使用本地 queue 异步写入")
        except Exception:
            logger.debug("[TraceWriter] Redis 初始化失败，降级本地 queue", exc_info=True)

    def _start_worker(self):
        self._worker = threading.Thread(
            target=self._run_worker, daemon=True, name="trace-write-worker"
        )
        self._worker.start()

    def enqueue(self, record: Any) -> None:
        """将已处理好的 TraceRecord 推入异步写入队列。

        必须在调用前完成数据质量处理（leaked span 关闭等）。
        同时捕获当前 store 单例引用，避免 worker 写入时 store 已被测试替换。
        """
        data = _serialize_record(record)
        stores = self._capture_stores()
        if self._use_redis and self._redis is not None:
            try:
                payload = json.dumps(data, ensure_ascii=False, default=str)
                self._redis.xadd(
                    f"agent:{_STREAM_KEY}",
                    {"data": payload},
                    maxlen=10000,
                )
                return
            except Exception:
                logger.debug("[TraceWriter] Redis XADD 失败，降级本地 queue", exc_info=True)
                self._use_redis = False

        try:
            self._local_queue.put_nowait((data, stores))
        except queue.Full:
            logger.warning("[TraceWriter] 本地 queue 已满（10000），丢弃 trace")

    @staticmethod
    def _capture_stores() -> tuple:
        """捕获当前 store 单例引用（避免 worker 延迟查找导致跨测试污染）。"""
        from backend.observability.trace_store import get_trace_store
        from backend.observability.analytics_store import get_analytics_store
        return (get_trace_store(), get_analytics_store())

    def _run_worker(self):
        """后台守护线程：批量消费 → 持久化。"""
        logger.info("[TraceWriter] worker 启动")
        while not self._stopped:
            try:
                batch = self._read_batch()
                if batch:
                    self._flush_batch(batch)
                else:
                    time.sleep(_FLUSH_INTERVAL_S)
            except Exception:
                logger.warning("[TraceWriter] worker 异常", exc_info=True)
                time.sleep(_FLUSH_INTERVAL_S)
        logger.info("[TraceWriter] worker 退出")

    def _read_batch(self) -> list[tuple[dict, tuple]]:
        """从 Redis 或本地 queue 读取一批待写入的 trace。"""
        batch: list[tuple[dict, tuple]] = []

        if self._use_redis and self._redis is not None:
            try:
                stream_key = f"agent:{_STREAM_KEY}"
                result = self._redis.xread(
                    {stream_key: "0"},
                    count=_BATCH_SIZE,
                    block=int(_FLUSH_INTERVAL_S * 1000),
                )
                if result:
                    stores = self._capture_stores()
                    for _stream_name, messages in result:
                        for msg_id, fields in messages:
                            data = json.loads(fields.get("data", "{}"))
                            batch.append((data, stores))
                            self._redis.xack(stream_key, "agents", msg_id)
                return batch
            except Exception:
                logger.debug("[TraceWriter] Redis XREAD 失败，降级本地 queue", exc_info=True)
                self._use_redis = False

        while len(batch) < _BATCH_SIZE:
            try:
                item = self._local_queue.get_nowait()
                if item is None:
                    break
                batch.append(item)
            except queue.Empty:
                break
        return batch

    def _flush_batch(self, batch: list[tuple[dict, tuple]]) -> None:
        """将一批 trace 持久化到 Langfuse + SQLite + Analytics。"""
        for data, stores in batch:
            trace_store, analytics_store = stores
            try:
                from backend.observability.langfuse_exporter import get_langfuse_exporter
                get_langfuse_exporter().export_trace_dict(data)
            except Exception:
                logger.warning("[TraceWriter] Langfuse 上报失败", exc_info=True)
            try:
                trace_store.save_dict(data)
            except Exception:
                logger.warning("[TraceWriter] SQLite 持久化失败", exc_info=True)
            try:
                analytics_store.save_dict(data)
            except Exception:
                logger.warning("[TraceWriter] Analytics 写入失败", exc_info=True)

    def flush(self) -> None:
        """同步排空队列并持久化（测试用，生产路径由 worker 异步处理）。

        暂停 worker → 排空队列 → 同步写入 → 重启 worker，消除竞态。
        """
        self._stopped = True
        if self._worker and self._worker.is_alive():
            self._local_queue.put_nowait(None)
            self._worker.join(timeout=5)

        all_items: list[tuple[dict, tuple]] = []
        while True:
            batch = self._read_batch()
            if not batch:
                break
            all_items.extend(batch)
        if all_items:
            self._flush_batch(all_items)

        self._stopped = False
        self._start_worker()

    def shutdown(self):
        """排空队列并停止 worker（进程退出时调用）。"""
        self._stopped = True
        if self._worker and self._worker.is_alive():
            self._local_queue.put_nowait(None)
            self._worker.join(timeout=5)


_trace_write_queue: TraceWriteQueue | None = None
_queue_lock = threading.Lock()


def get_trace_write_queue() -> TraceWriteQueue:
    global _trace_write_queue
    if _trace_write_queue is not None:
        return _trace_write_queue
    with _queue_lock:
        if _trace_write_queue is not None:
            return _trace_write_queue
        _trace_write_queue = TraceWriteQueue()
        return _trace_write_queue
