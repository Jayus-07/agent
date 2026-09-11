"""infra.messaging.consumer — Kafka 入站消费者（WhatsApp 闭环）。

闭环链路（docs/architecture-overview.md §2/§4）：
  Meta webhook → Java 落库 → channel.whatsapp.inbound
    → 本消费者 → MultiAgentSystem.ask() 生成回复
    → ai.reply.events → Java AiReplyConsumer → WhatsAppSender 发送

设计（与 producer/redis 同款降级模式）：
- KAFKA_ENABLED=false 或 KAFKA_CONSUMER_ENABLED=false 时不启动
- 连接失败 60s 冷却重连，不阻塞主进程
- auto_offset_reset=latest：只处理启动后的新消息（不回放历史积压，
  避免对几小时前的旧消息生成迟到的回复）；漏消息由 Meta webhook 重试兜底
- 幂等：Redis SETNX（wa:processed:{message_id}，24h TTL）；
  Redis 不可用时退化为进程内 LRU 去重
- trace：ask() 内部已有完整 trace，消费层不再开独立 trace（避免双 trace），
  观测走 kafka_consumer_* 指标
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any

from backend.shared.logger import logger

_POLL_TIMEOUT_S = 1.0
_RECONNECT_COOLDOWN_S = 60.0
_LAG_REPORT_INTERVAL_S = 30.0
_DEDUP_TTL_S = 86400
_DEDUP_MAX_INPROC = 10_000

_thread: threading.Thread | None = None
_start_lock = threading.Lock()
_stop_event = threading.Event()

# 进程内去重兜底（Redis 不可用时）
_inproc_dedup: OrderedDict[str, float] = OrderedDict()


def start_consumer_worker() -> None:
    """启动消费后台线程（幂等；开关关闭时静默跳过）。"""
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return
        from backend.config.messaging import KAFKA_CONSUMER_ENABLED, KAFKA_ENABLED

        if not KAFKA_ENABLED or not KAFKA_CONSUMER_ENABLED:
            logger.info("[KafkaConsumer] disabled (KAFKA_ENABLED/KAFKA_CONSUMER_ENABLED)")
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_consume_loop, daemon=True, name="kafka-whatsapp-consumer",
        )
        _thread.start()
        logger.info("[KafkaConsumer] worker thread started")


def stop_consumer_worker() -> None:
    """通知消费线程退出（测试/优雅停机用）。"""
    _stop_event.set()


def _consume_loop() -> None:
    """消费主循环：连接失败按冷却周期重连，退出靠 _stop_event。"""
    from backend.config.messaging import KAFKA_BOOTSTRAP_SERVERS, KAFKA_CONSUMER_GROUP

    while not _stop_event.is_set():
        consumer = None
        try:
            from kafka import KafkaConsumer

            consumer = KafkaConsumer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id=KAFKA_CONSUMER_GROUP,
                auto_offset_reset="latest",
                consumer_timeout_ms=1000,  # 配合 _stop_event 轮询退出
            )
            from backend.config.messaging import TOPIC_WHATSAPP_INBOUND
            consumer.subscribe([TOPIC_WHATSAPP_INBOUND])
            logger.info(f"[KafkaConsumer] connected: {KAFKA_BOOTSTRAP_SERVERS}")
            _run_poll_loop(consumer, TOPIC_WHATSAPP_INBOUND)
        except Exception as e:
            logger.warning(
                f"[KafkaConsumer] loop failed, reconnect in {_RECONNECT_COOLDOWN_S}s: {e}"
            )
            if _stop_event.wait(_RECONNECT_COOLDOWN_S):
                break
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass


def _run_poll_loop(consumer, topic: str) -> None:
    from backend.config.messaging import TOPIC_WHATSAPP_INBOUND

    last_lag_report = 0.0
    while not _stop_event.is_set():
        batches = consumer.poll(timeout_ms=int(_POLL_TIMEOUT_S * 1000))
        for _tp, records in batches.items():
            for record in records:
                try:
                    _handle_event(record.value, topic)
                except Exception:
                    from backend.observability.metrics import kafka_consumer_processed_total
                    kafka_consumer_processed_total.labels(topic=topic, status="error").inc()
                    logger.warning("[KafkaConsumer] handle failed", exc_info=True)

        now = time.monotonic()
        if now - last_lag_report >= _LAG_REPORT_INTERVAL_S:
            last_lag_report = now
            _report_lag(consumer, TOPIC_WHATSAPP_INBOUND)


def _report_lag(consumer, topic: str) -> None:
    """上报消费滞后（总滞后 = 各分区 highwater - position 之和）。"""
    try:
        from backend.observability.metrics import ai_kafka_consumer_lag

        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return
        from kafka import TopicPartition

        tps = [TopicPartition(topic, p) for p in partitions]
        end_offsets = consumer.end_offsets(tps)
        total_lag = 0
        for tp, end in end_offsets.items():
            pos = consumer.position(tp) or 0
            lag = max(0, end - pos)
            total_lag += lag
            ai_kafka_consumer_lag.labels(topic=topic, partition=str(tp.partition)).set(lag)
        if total_lag > 0:
            logger.info(f"[KafkaConsumer] lag={total_lag} on {topic}")
    except Exception:
        logger.debug("[KafkaConsumer] lag report failed", exc_info=True)


def _handle_event(raw: bytes | str, topic: str) -> None:
    """处理单条入站事件（异常向上抛，由 poll 循环计数 error）。"""
    from backend.observability.metrics import kafka_consumer_processed_total

    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[KafkaConsumer] unparsable event dropped")
        kafka_consumer_processed_total.labels(topic=topic, status="error").inc()
        return

    event_type = event.get("event_type", "")
    payload: dict[str, Any] = event.get("payload") or {}

    if event_type != "whatsapp.message.inbound":
        kafka_consumer_processed_total.labels(topic=topic, status="skipped").inc()
        return

    message_id = payload.get("message_id") or ""
    text = payload.get("text") or ""
    conversation_id = event.get("conversation_id") or payload.get("conversation_id") or ""
    user_id = event.get("user_id") or payload.get("external_user_id") or ""
    msg_type = payload.get("msg_type") or "text"

    if msg_type != "text" or not text.strip():
        logger.info(f"[KafkaConsumer] skip non-text message: {msg_type}")
        kafka_consumer_processed_total.labels(topic=topic, status="skipped").inc()
        return

    if _is_duplicate(message_id, topic):
        return

    started = time.perf_counter()
    answer = _generate_reply(text, conversation_id, user_id)
    elapsed = time.perf_counter() - started

    from backend.observability.metrics import kafka_consumer_duration_seconds
    kafka_consumer_duration_seconds.observe(elapsed)

    if not answer:
        kafka_consumer_processed_total.labels(topic=topic, status="skipped").inc()
        logger.warning(f"[KafkaConsumer] empty answer for {message_id}, no reply sent")
        return

    _publish_reply(conversation_id, user_id, message_id, answer)
    kafka_consumer_processed_total.labels(topic=topic, status="ok").inc()
    logger.info(
        f"[KafkaConsumer] replied to {message_id} "
        f"(conversation={conversation_id}, {elapsed:.1f}s)"
    )


def _is_duplicate(message_id: str, topic: str) -> bool:
    """幂等检查：Redis SETNX 优先，不可用退化为进程内 LRU。"""
    from backend.observability.metrics import kafka_consumer_processed_total

    if not message_id:
        return False

    from backend.infra.redis.client import get_redis
    redis = get_redis()
    if redis is not None:
        key = f"wa:processed:{message_id}"
        try:
            if not redis.set(key, 1, nx=True, ex=_DEDUP_TTL_S):
                kafka_consumer_processed_total.labels(topic=topic, status="duplicate").inc()
                return True
            return False
        except Exception:
            logger.warning("[KafkaConsumer] redis dedup failed, fallback to inproc")

    # 进程内 LRU 兜底
    now = time.monotonic()
    if message_id in _inproc_dedup:
        _inproc_dedup.move_to_end(message_id)
        kafka_consumer_processed_total.labels(topic=topic, status="duplicate").inc()
        return True
    _inproc_dedup[message_id] = now
    while len(_inproc_dedup) > _DEDUP_MAX_INPROC:
        _inproc_dedup.popitem(last=False)
    return False


def _generate_reply(text: str, conversation_id: str, user_id: str) -> str:
    """调 Agent 生成回复。任何异常返回空串（不发送）。"""
    try:
        from backend.app.api.deps import get_multi_agent
        agent = get_multi_agent()
        # WhatsApp 会话以 conversation_id 作为 session_id，渠道用户 ID 直接复用
        return agent.ask(
            text,
            session_id=conversation_id or "whatsapp",
            user_id=user_id or "whatsapp",
        )
    except Exception:
        logger.warning("[KafkaConsumer] agent ask failed", exc_info=True)
        return ""


def _publish_reply(conversation_id: str, user_id: str, reply_to: str, answer: str) -> None:
    """发布 AI 回复事件，供 Java AiReplyConsumer 经 WhatsAppSender 发送。"""
    from backend.config.messaging import TOPIC_AI_REPLY_EVENTS
    from backend.infra.messaging.kafka import publish_event

    publish_event(
        TOPIC_AI_REPLY_EVENTS,
        "ai.reply.created",
        conversation_id or None,
        user_id or None,
        {
            "channel": "whatsapp",
            "conversation_id": conversation_id,
            "external_user_id": user_id,
            "reply_to_message_id": reply_to,
            "text": answer,
        },
    )
