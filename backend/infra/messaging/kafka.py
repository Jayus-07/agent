"""infra.messaging.kafka — Kafka producer 单例。

设计（对齐 infra/redis/client.py 的降级模式）：
- 双重检查锁懒初始化
- KAFKA_ENABLED=false 或连接失败时返回 None，调用方优雅跳过
- probe cooldown：连接失败后 60s 内不再重试（避免日志洪泛）
- publish() 为 fire-and-forget：事件是旁路，绝不阻塞/失败主流程

事件契约见 backend/config/messaging.py 的 topic 常量与 docs/architecture-overview.md。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.shared.logger import logger

_producer = None
_lock = threading.Lock()
_last_probe_fail: float = 0.0
_PROBE_COOLDOWN = 60.0


def get_kafka_producer():
    """获取 KafkaProducer 单例。

    Returns:
        kafka.KafkaProducer | None: 不可用时返回 None。
    """
    global _producer, _last_probe_fail

    if _producer is not None:
        return _producer

    now = time.monotonic()
    if now - _last_probe_fail < _PROBE_COOLDOWN:
        return None

    with _lock:
        if _producer is not None:
            return _producer

        from backend.config.messaging import KAFKA_ENABLED
        if not KAFKA_ENABLED:
            return None

        try:
            from kafka import KafkaProducer  # kafka-python，纯 Python 实现

            from backend.config.messaging import (
                KAFKA_BOOTSTRAP_SERVERS,
                KAFKA_CLIENT_ID,
            )

            def _serialize(value: dict) -> bytes:
                return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")

            _producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                client_id=KAFKA_CLIENT_ID,
                value_serializer=_serialize,
                acks="all",
                retries=3,
            )
            logger.info(f"[Kafka] connected: {KAFKA_BOOTSTRAP_SERVERS}")
            return _producer
        except Exception as e:
            _last_probe_fail = time.monotonic()
            _producer = None
            logger.warning(f"[Kafka] connection failed (cooldown {_PROBE_COOLDOWN}s): {e}")
            return None


def is_kafka_available() -> bool:
    """快速检查 Kafka producer 是否可用（不抛异常）。"""
    return get_kafka_producer() is not None


def publish_event(
    topic: str,
    event_type: str,
    conversation_id: str | None,
    user_id: str | None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """发布一条业务事件（fire-and-forget）。

    事件契约与 business-service KafkaEventPublisher 一致：
      { event_id, event_type, occurred_at, source, conversation_id, user_id, payload }

    Returns:
        bool: 是否成功提交到 producer（发送异步进行，失败仅记日志）。
    """
    producer = get_kafka_producer()
    if producer is None:
        return False

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "source": "ai-service",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "payload": payload or {},
    }
    try:
        # key = conversation_id 保证同一会话事件有序；发送回调只记日志
        future = producer.send(topic, key=conversation_id, value=event)
        future.add_errback(
            lambda e: logger.warning(f"[Kafka] async send failed for {event_type}: {e}")
        )
        return True
    except Exception as e:
        logger.warning(f"[Kafka] publish {event_type} failed: {e}")
        return False
