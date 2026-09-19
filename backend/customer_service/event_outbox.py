"""客服实时事件的 Redis Stream 补偿队列（P3）。

事件先广播、后补偿的关键约束：只有 PostgreSQL 事件表提交成功后，
才 ACK 并删除 Stream 消息；Worker/进程中断时消息留在 pending，下一轮
通过 XAUTOCLAIM 重新领取。事件表的 event_id 唯一约束负责最终幂等。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from backend.config.redis import REDIS_KEY_PREFIX
from backend.shared.logger import logger

OUTBOX_STREAM = f"{REDIS_KEY_PREFIX}cs:event_outbox"
OUTBOX_GROUP = "cs-event-compensation"
OUTBOX_MAXLEN = 10000
OUTBOX_CLAIM_IDLE_MS = 60000


def _redis():
    from backend.infra.redis.client import get_redis

    return get_redis()


def _ensure_group(redis) -> None:
    try:
        redis.xgroup_create(
            OUTBOX_STREAM, OUTBOX_GROUP, id="0-0", mkstream=True,
        )
    except Exception as exc:
        # 多个 API/Worker 同时初始化时，只有 BUSYGROUP 是预期竞态；
        # 其他 Redis 错误必须继续向上暴露，避免假装队列已就绪。
        if "BUSYGROUP" not in str(exc).upper():
            raise


def enqueue_event(*, envelope: dict, payload: dict) -> bool:
    """把 PG 尚未落库的事件写入 Redis Stream。"""
    redis = _redis()
    if redis is None:
        logger.error("[CSEventOutbox] Redis unavailable, event compensation skipped")
        return False

    record = {
        "event_id": str(envelope.get("event_id") or ""),
        "type": str(envelope.get("type") or ""),
        "ts": str(envelope.get("ts") or ""),
        "conversation_id": str(payload.get("conversation_id") or ""),
        "payload": payload,
    }
    if not record["event_id"] or not record["conversation_id"]:
        logger.error("[CSEventOutbox] invalid event, missing id/conversation")
        return False

    try:
        _ensure_group(redis)
        redis.xadd(
            OUTBOX_STREAM,
            {"event": json.dumps(record, ensure_ascii=False, default=str)},
            maxlen=OUTBOX_MAXLEN,
            approximate=True,
        )
        return True
    except Exception:
        logger.error(
            "[CSEventOutbox] enqueue failed: event_id=%s",
            record["event_id"],
            exc_info=True,
        )
        return False


def _decode_message(message: tuple[Any, Any]) -> dict:
    _message_id, fields = message
    raw = fields.get("event") if hasattr(fields, "get") else None
    if raw is None and hasattr(fields, "get"):
        raw = fields.get(b"event")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not raw:
        raise ValueError("event outbox message has no event field")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("event outbox payload must be an object")
    if not event.get("event_id") or not event.get("conversation_id"):
        raise ValueError("event outbox event missing event_id/conversation_id")
    return event


def _persist_event_record(event: dict) -> int:
    """把补偿事件写入 PostgreSQL；异常向 drain 调用方传播。"""
    from backend.customer_service._db_loop import run_sync
    from backend.customer_service.repository.event_repo import EventRepository
    from backend.memory.database import AsyncSessionLocal

    async def _persist() -> int:
        async with AsyncSessionLocal() as db:
            return await EventRepository(db).append(
                conversation_id=str(event["conversation_id"]),
                event_id=str(event["event_id"]),
                type=str(event["type"]),
                payload=dict(event.get("payload") or {}),
            )

    operation = _persist()
    try:
        return int(run_sync(operation))
    except Exception:
        if getattr(operation, "cr_frame", None) is not None:
            operation.close()
        raise
    finally:
        if getattr(operation, "cr_frame", None) is not None:
            operation.close()


def _is_xautoclaim_unavailable(exc: Exception) -> bool:
    """只把明确的 XAUTOCLAIM 能力缺失视为可降级错误。"""
    if isinstance(exc, (AttributeError, NotImplementedError, TypeError)):
        return True
    message = str(exc).lower()
    return "unknown command" in message and "xautoclaim" in message


def _claim_with_legacy_api(
    redis,
    *,
    consumer: str,
    limit: int,
    min_idle_ms: int,
) -> list[tuple[Any, Any]]:
    """在 Redis 不支持 XAUTOCLAIM 时，用 XPENDING + XCLAIM 接管旧消息。"""
    pending = redis.xpending_range(
        OUTBOX_STREAM,
        OUTBOX_GROUP,
        min="-",
        max="+",
        count=limit,
    )
    message_ids: list[Any] = []
    for item in pending or []:
        if not isinstance(item, dict):
            continue
        message_id = item.get("message_id")
        if message_id is None:
            message_id = item.get(b"message_id")
        idle_ms = item.get("time_since_delivered")
        if idle_ms is None:
            idle_ms = item.get(b"time_since_delivered")
        if message_id is None or idle_ms is None:
            continue
        if int(idle_ms) >= min_idle_ms:
            message_ids.append(message_id)
        if len(message_ids) >= limit:
            break

    if not message_ids:
        return []
    return list(
        redis.xclaim(
            OUTBOX_STREAM,
            OUTBOX_GROUP,
            consumer,
            min_idle_ms,
            message_ids,
        )
        or []
    )


def _collect_messages(redis, *, consumer: str, limit: int, min_idle_ms: int):
    """先接管旧 pending，再读取新消息；同一轮按 message id 去重。"""
    messages: list[tuple[Any, Any]] = []
    try:
        claimed = redis.xautoclaim(
            OUTBOX_STREAM,
            OUTBOX_GROUP,
            consumer,
            min_idle_ms,
            "0-0",
            count=limit,
        )
    except Exception as exc:
        if not _is_xautoclaim_unavailable(exc):
            raise
        claimed = (
            "0-0",
            _claim_with_legacy_api(
                redis,
                consumer=consumer,
                limit=limit,
                min_idle_ms=min_idle_ms,
            ),
            [],
        )
    if isinstance(claimed, tuple) and len(claimed) >= 2:
        messages.extend(claimed[1] or [])

    remaining = max(0, limit - len(messages))
    if remaining:
        fresh = redis.xreadgroup(
            OUTBOX_GROUP,
            consumer,
            {OUTBOX_STREAM: ">"},
            count=remaining,
            block=0,
        )
        for _stream, stream_messages in fresh or []:
            messages.extend(stream_messages or [])

    unique: list[tuple[Any, Any]] = []
    seen: set[str] = set()
    for message_id, fields in messages:
        key = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
        if key not in seen:
            seen.add(key)
            unique.append((message_id, fields))
    return unique[:limit]


def drain_pending_events(
    *,
    limit: int = 100,
    min_idle_ms: int = OUTBOX_CLAIM_IDLE_MS,
    consumer: str | None = None,
) -> dict[str, int | bool]:
    """补偿一批事件；PG 失败时保留 pending，不 ACK。"""
    redis = _redis()
    if redis is None:
        return {"ok": False, "processed": 0, "failed": 0}

    consumer_name = consumer or f"cs-compensator-{uuid.uuid4().hex[:12]}"
    try:
        _ensure_group(redis)
        messages = _collect_messages(
            redis,
            consumer=consumer_name,
            limit=max(1, int(limit)),
            min_idle_ms=max(0, int(min_idle_ms)),
        )
    except Exception:
        logger.error("[CSEventOutbox] read pending events failed", exc_info=True)
        return {"ok": False, "processed": 0, "failed": 0}

    processed = 0
    failed = 0
    for message_id, _fields in messages:
        try:
            event = _decode_message((message_id, _fields))
            _persist_event_record(event)
            redis.xack(OUTBOX_STREAM, OUTBOX_GROUP, message_id)
            try:
                redis.xdel(OUTBOX_STREAM, message_id)
            except Exception:
                # ACK 已成功，删除失败不会造成业务重复；保留日志便于清理。
                logger.warning(
                    "[CSEventOutbox] xdel failed after ack: %s",
                    message_id,
                    exc_info=True,
                )
            processed += 1
        except Exception:
            failed += 1
            logger.warning(
                "[CSEventOutbox] persist failed, keep pending: %s",
                message_id,
                exc_info=True,
            )
            # PG 故障期间不要继续轰炸数据库；剩余消息留给下一次领取。
            break

    return {"ok": failed == 0, "processed": processed, "failed": failed}
