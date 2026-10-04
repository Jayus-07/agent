"""异步 Trace 写入队列 — Redis Streams 优先，本地 queue 降级。

TraceCollector.finish() 的数据质量处理（leaked span 关闭、status 聚合等）
仍在调用线程同步完成；此模块只接管后续的持久化步骤（SQLite +
Analytics），将阻塞 I/O 从请求路径移入后台 worker。

Redis 可用时：XADD → stream `agent:trace:write`，worker 通过 Consumer Group 批量消费。
Redis 不可用时：queue.Queue 本地 fallback，worker get() 逐条消费。
两者都不可用时：同步写入（最差情况，退化为旧行为）。
"""
from __future__ import annotations

import json
import queue
import random
import threading
import time
from typing import Any

from backend.config.observability import TRACE_SAMPLING_RATE
from backend.shared.logger import logger

_STREAM_KEY = "trace:write"
_CONSUMER_GROUP = "trace-writers"
_BATCH_SIZE = 50
_FLUSH_INTERVAL_S = 2.0
_CLAIM_MIN_IDLE_MS = 60_000
# flush() 排空循环的轮数上限（防御性）：即使上游持续返回非空也必须收敛，
# 否则无界循环会把进程内存吃光并永久挂起。
_MAX_FLUSH_ROUNDS = 200


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


def _should_keep(record: Any) -> bool:
    """采样决策：error trace 始终保留，其余按 TRACE_SAMPLING_RATE 概率保留。"""
    get = lambda k, d=None: record.get(k, d) if isinstance(record, dict) else getattr(record, k, d)
    if get("error") or get("status") == "error":
        return True
    if TRACE_SAMPLING_RATE >= 1.0:
        return True
    if TRACE_SAMPLING_RATE <= 0.0:
        return False
    return random.random() < TRACE_SAMPLING_RATE


class TraceWriteQueue:
    """后台 trace 持久化队列单例。"""

    def __init__(self):
        self._local_queue: queue.Queue[tuple[dict, tuple] | None] = queue.Queue(maxsize=10000)
        self._use_redis = False
        self._redis = None
        self._worker: threading.Thread | None = None
        self._stopped = False
        self._consumer_group = _CONSUMER_GROUP
        self._consumer_name = self._build_consumer_name()
        self._group_ready = False
        self._try_init_redis()
        self._start_worker()

    @staticmethod
    def _build_consumer_name() -> str:
        """生成进程内唯一的消费者名，避免多副本互相抢占同一消费者。"""
        import os
        import socket
        import uuid

        return (
            f"{socket.gethostname()}-{os.getpid()}-"
            f"{uuid.uuid4().hex[:8]}"
        )

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
        if not _should_keep(record):
            return

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
        from backend.observability.analytics_store import get_analytics_store
        from backend.observability.trace_store import get_trace_store
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

    def _ensure_consumer_group(self, stream_key: str) -> None:
        """确保 Trace Stream 的 Consumer Group 存在。"""
        if self._group_ready:
            return
        try:
            self._redis.xgroup_create(
                stream_key,
                self._consumer_group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            # 多个 app/worker 并发启动时，只有一个能创建 group；其余收到
            # BUSYGROUP 后继续使用已存在的 group。
            if "BUSYGROUP" not in str(exc).upper():
                raise
        self._group_ready = True

    def _claim_pending_messages(self, stream_key: str) -> list[tuple[str, dict]]:
        """接管超时未 ACK 的消息，避免 worker 崩溃后消息永久滞留 PEL。"""
        xautoclaim = getattr(self._redis, "xautoclaim", None)
        if not callable(xautoclaim):
            return []
        try:
            result = xautoclaim(
                stream_key,
                self._consumer_group,
                self._consumer_name,
                _CLAIM_MIN_IDLE_MS,
                start_id="0-0",
                count=_BATCH_SIZE,
            )
            # redis-py 返回 (next_start_id, messages, deleted_ids)。
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                return list(result[1] or [])
        except Exception:
            logger.debug("[TraceWriter] 接管 PEL 消息失败", exc_info=True)
        return []

    def _read_batch(self) -> list[tuple[dict, tuple, str | None]]:
        """从 Redis 或本地 queue 读取一批待写入的 trace。"""
        batch: list[tuple[dict, tuple, str | None]] = []

        if self._use_redis and self._redis is not None:
            try:
                stream_key = f"agent:{_STREAM_KEY}"
                self._ensure_consumer_group(stream_key)
                claimed = self._claim_pending_messages(stream_key)
                if claimed:
                    result = [(stream_key, claimed)]
                else:
                    result = self._redis.xreadgroup(
                        self._consumer_group,
                        self._consumer_name,
                        {stream_key: ">"},
                        count=_BATCH_SIZE,
                        block=int(_FLUSH_INTERVAL_S * 1000),
                    )
                if result:
                    stores = self._capture_stores()
                    for _stream_name, messages in result:
                        for msg_id, fields in messages:
                            raw_data = fields.get("data", "{}")
                            if isinstance(raw_data, bytes):
                                raw_data = raw_data.decode("utf-8")
                            data = json.loads(raw_data)
                            # 第三个元素保留消息 ID，只有 trace_store 成功后才 ACK。
                            batch.append((data, stores, msg_id))
                return batch
            except Exception:
                logger.debug(
                    "[TraceWriter] Redis Consumer Group 读取失败，降级本地 queue",
                    exc_info=True,
                )
                self._use_redis = False

        while len(batch) < _BATCH_SIZE:
            try:
                item = self._local_queue.get_nowait()
                if item is None:
                    break
                batch.append((*item, None))
            except queue.Empty:
                break
        return batch

    def _flush_batch(self, batch: list[tuple[dict, tuple, str | None]]) -> None:
        """将一批 trace 持久化到 SQLite + Analytics。"""
        for item in batch:
            if len(item) == 2:
                # 兼容历史测试/调用方构造的本地队列二元组。
                data, stores = item
                msg_id = None
            else:
                data, stores, msg_id = item
            trace_store, analytics_store = stores
            trace_saved = False
            try:
                trace_store.save_dict(data)
                trace_saved = True
            except Exception:
                logger.warning("[TraceWriter] SQLite 持久化失败", exc_info=True)
            try:
                analytics_store.save_dict(data)
            except Exception:
                logger.warning("[TraceWriter] Analytics 写入失败", exc_info=True)
            try:
                from backend.observability.pg_trace_sink import write_trace
                write_trace(data)
            except Exception:
                logger.debug("[TraceWriter] PG mirror 写入失败", exc_info=True)

            if msg_id is not None and trace_saved and self._redis is not None:
                try:
                    self._redis.xack(
                        f"agent:{_STREAM_KEY}",
                        self._consumer_group,
                        msg_id,
                    )
                except Exception:
                    # 未 ACK 的消息会留在 PEL 中，可由后续消费者重新领取，
                    # 因此这里不能把持久化成功伪装成消费成功。
                    logger.warning(
                        "[TraceWriter] Trace Stream ACK 失败，消息将保留在 PEL",
                        extra={"message_id": msg_id},
                        exc_info=True,
                    )

    def flush(self) -> None:
        """同步排空队列并持久化（测试用，生产路径由 worker 异步处理）。

        暂停 worker → 排空队列 → 同步写入 → 重启 worker，消除竞态。
        """
        self._stopped = True
        if self._worker and self._worker.is_alive():
            self._local_queue.put_nowait(None)
            self._worker.join(timeout=5)

        # 只排空本地队列。Redis 流 `agent:trace:write` 是跨进程共享的公共通道，
        # 里面的历史消息不属于本次 flush 的调用方：一来它们没有 store 归属信息，
        # 会被按"读取时捕获的 store"写入而污染调用方；二来公共流几乎不可能为空，
        # 排空循环将永不收敛（曾导致全量测试卡死、进程内存涨到 9.8GB）。
        # Redis 路径的消息由 worker 异步消费，不需要 flush 代劳。
        saved_use_redis = self._use_redis
        self._use_redis = False
        all_items: list[tuple[dict, tuple]] = []
        try:
            for _ in range(_MAX_FLUSH_ROUNDS):
                batch = self._read_batch()
                if not batch:
                    break
                all_items.extend(batch)
            else:
                logger.warning(
                    "[TraceWriter] flush 达到 %d 轮上限仍未排空，剩余消息交回 worker 处理",
                    _MAX_FLUSH_ROUNDS,
                )
        finally:
            self._use_redis = saved_use_redis
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
