"""Prompt Runtime 热更新：DB epoch + Redis 推送/拉取兜底。

DB 保存 epoch 与补偿事件，Redis 只负责低延迟传播。Redis 或刷新失败时保持
当前进程快照继续服务（fail-open），请求入口的拉取检查负责最终追平。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from typing import Any

from backend.config.redis import (
    PROMPT_EPOCH_CHECK_TTL,
    PROMPT_HOT_RELOAD_ENABLED,
    PROMPT_PUBSUB_CHANNEL,
    PROMPT_RUNTIME_HEARTBEAT_INTERVAL,
    PROMPT_RUNTIME_HEARTBEAT_TTL,
    REDIS_KEY_PREFIX,
)
from backend.infra.redis.client import get_redis
from backend.memory.database import AsyncSessionLocal
from backend.prompts.service import prompt_service
from backend.shared.logger import logger

PROMPT_EPOCH_KEY = f"{REDIS_KEY_PREFIX}prompt:epoch"
PROMPT_CHANGED_CHANNEL = PROMPT_PUBSUB_CHANNEL
PROMPT_RUNTIME_KEY_PREFIX = f"{REDIS_KEY_PREFIX}prompt:runtime:"
_REFRESH_DEBOUNCE_SECONDS = 2.0

_state_lock = threading.RLock()
_listener_lock = threading.Lock()
_listener_thread: threading.Thread | None = None
_heartbeat_thread: threading.Thread | None = None
_local_epoch = 0
_last_epoch_check_at = 0.0
_last_refresh_at = 0.0
_refresh_lock = threading.Lock()
_runtime_instance_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _runtime_service_name() -> str:
    explicit = os.getenv("PROMPT_RUNTIME_NAME") or os.getenv("SERVICE_NAME")
    if explicit:
        return explicit
    if os.getenv("WORKER_METRICS_PORT"):
        return "worker"
    if os.getenv("RAG_MODE", "").strip().lower() == "local":
        return "rag-service"
    return "app"


_runtime_name = _runtime_service_name()


async def _persist_epoch_event(*, key: str, actor: str) -> int | None:
    """在 DB 中递增 epoch 并留下待发送事件；治理旁路失败不阻断发布。"""
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    INSERT INTO prompt_runtime_state
                        (id, global_epoch, updated_by)
                    VALUES (1, 1, :actor)
                    ON CONFLICT (id) DO UPDATE SET
                        global_epoch = prompt_runtime_state.global_epoch + 1,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    RETURNING global_epoch
                    """
                ),
                {"actor": actor or "system"},
            )
            epoch = int(result.scalar_one())
            await session.execute(
                text(
                    """
                    INSERT INTO prompt_reload_events
                        (epoch, event_type, status)
                    VALUES (:epoch, 'prompt.changed', 'pending')
                    """
                ),
                {"epoch": epoch},
            )
            await session.commit()
            return epoch
    except Exception as exc:  # noqa: BLE001 — 治理旁路软失败
        logger.warning("[PromptHotReload] persist epoch failed: %s", exc)
        return None


async def _read_db_epoch() -> int | None:
    """读取 DB 权威 epoch，供 Redis 丢失/落后时的最终一致性兜底。"""
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT global_epoch FROM prompt_runtime_state WHERE id = 1")
            )
            value = result.scalar_one_or_none()
            return int(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001 — 兜底读取失败保持旧快照
        logger.debug("[PromptHotReload] DB epoch read failed: %s", exc)
        return None


async def _mark_event_published(epoch: int) -> None:
    """标记 epoch 已发出；找不到表/DB 不可用时静默保留 pending。"""
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    """
                    UPDATE prompt_reload_events
                    SET status = 'published'
                    WHERE epoch = :epoch AND status = 'pending'
                    """
                ),
                {"epoch": epoch},
            )
            await session.commit()
    except Exception:
        logger.debug("[PromptHotReload] mark event published failed", exc_info=True)


async def bump_prompt_epoch(key: str, actor: str = "system") -> int | None:
    """发布成功后的唯一 epoch bump 出口。

    先写 DB epoch，再 INCR Redis 并发布事件。任一旁路失败都不向业务抛异常。
    """
    if not PROMPT_HOT_RELOAD_ENABLED:
        return None

    db_epoch = await _persist_epoch_event(key=key, actor=actor)
    redis_epoch: int | None = None
    try:
        redis = get_redis()
        if redis is not None:
            if db_epoch is not None:
                # DB 是权威：先把 Redis 校准到 DB-1，再执行 INCR，避免
                # Redis 残留更大计数导致发布信号脱离真实版本。
                raw = redis.get(PROMPT_EPOCH_KEY)
                current = int(raw) if raw is not None else None
                if current != db_epoch - 1:
                    redis.set(PROMPT_EPOCH_KEY, max(0, db_epoch - 1))
            redis_epoch = int(redis.incr(PROMPT_EPOCH_KEY))
            if db_epoch is not None and redis_epoch != db_epoch:
                redis.set(PROMPT_EPOCH_KEY, db_epoch)
                redis_epoch = db_epoch
            epoch = redis_epoch if redis_epoch is not None else db_epoch
            if epoch is not None:
                payload = json.dumps(
                    {
                        "key": key,
                        "epoch": epoch,
                        "actor": actor or "system",
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                )
                redis.publish(PROMPT_CHANGED_CHANNEL, payload)
                await _mark_event_published(epoch)
                _set_local_epoch(epoch)
                return epoch
    except Exception as exc:  # noqa: BLE001 — Redis 旁路软失败
        logger.warning("[PromptHotReload] bump/publish failed: %s", exc)

    # DB 成功但 Redis 不可用：请求仍正常，恢复后由拉取/补偿机制追平。
    if db_epoch is not None:
        _set_local_epoch(db_epoch)
    return db_epoch


def _set_local_epoch(epoch: int) -> None:
    global _local_epoch
    with _state_lock:
        if epoch > _local_epoch:
            _local_epoch = int(epoch)


def local_epoch() -> int:
    with _state_lock:
        return _local_epoch


def prompt_runtime_metadata(versions: dict[str, int]) -> dict[str, Any]:
    """构建可写入 Trace 的请求级 Prompt Runtime 元数据。"""
    return {
        "epoch": local_epoch(),
        "versions": dict(versions),
        **prompt_service.snapshot_metadata(),
    }


def _runtime_payload() -> dict[str, Any]:
    metadata = prompt_service.snapshot_metadata()
    return {
        "name": _runtime_name,
        "instance_id": _runtime_instance_id,
        "epoch": local_epoch(),
        "versions": prompt_service.current_versions(),
        "snapshot_time": metadata.get("snapshot_time", ""),
        "reload_source": metadata.get("reload_source", "startup"),
        "updated_at": time.time(),
    }


def _write_runtime_heartbeat() -> bool:
    try:
        redis = get_redis()
        if redis is None:
            return False
        redis.set(
            f"{PROMPT_RUNTIME_KEY_PREFIX}{_runtime_instance_id}",
            json.dumps(_runtime_payload(), ensure_ascii=False),
            ex=PROMPT_RUNTIME_HEARTBEAT_TTL,
        )
        return True
    except Exception:  # noqa: BLE001 — 状态展示旁路软失败
        logger.debug("[PromptHotReload] runtime heartbeat failed", exc_info=True)
        return False


def _heartbeat_loop() -> None:
    while PROMPT_HOT_RELOAD_ENABLED:
        _write_runtime_heartbeat()
        time.sleep(PROMPT_RUNTIME_HEARTBEAT_INTERVAL)


def _start_runtime_heartbeat() -> None:
    global _heartbeat_thread
    with _listener_lock:
        if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
            return
        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name="prompt-runtime-heartbeat",
            daemon=True,
        )
        _heartbeat_thread.start()


def runtime_status() -> dict[str, Any]:
    """读取各进程 Prompt Runtime 心跳，Redis 不可用时返回本进程降级视图。"""
    redis = None
    processes: list[dict[str, Any]] = []
    try:
        redis = get_redis()
        if redis is not None:
            _write_runtime_heartbeat()
            raw_epoch = redis.get(PROMPT_EPOCH_KEY)
            epoch = int(raw_epoch) if raw_epoch is not None else local_epoch()
            for key in redis.scan_iter(match=f"{PROMPT_RUNTIME_KEY_PREFIX}*"):
                raw = redis.get(key)
                if not raw:
                    continue
                payload = json.loads(raw)
                age = max(0.0, time.time() - float(payload.get("updated_at", 0)))
                payload["status"] = "healthy" if age <= PROMPT_RUNTIME_HEARTBEAT_TTL else "stale"
                payload["age_seconds"] = round(age, 1)
                processes.append(payload)
            return {"epoch": epoch, "processes": processes}
    except Exception:  # noqa: BLE001 — 管理态查询不能影响业务
        logger.debug("[PromptHotReload] runtime status read failed", exc_info=True)

    payload = _runtime_payload()
    payload["status"] = "unknown" if redis is None else "degraded"
    payload["age_seconds"] = 0.0
    return {"epoch": local_epoch(), "processes": [payload]}


async def _refresh_if_newer(epoch: int, *, source: str) -> bool:
    """仅在 authoritative epoch 更新时刷新，进程内 2 秒防抖。"""
    global _last_refresh_at
    if epoch <= local_epoch():
        return False
    # 监听线程与请求线程可能各自拥有不同事件循环，使用线程锁避免
    # asyncio.Lock 绑定单一事件循环后在跨线程刷新时失效。
    with _refresh_lock:
        if epoch <= local_epoch():
            return False
        now = time.monotonic()
        if now - _last_refresh_at < _REFRESH_DEBOUNCE_SECONDS:
            return False
        try:
            before = local_epoch()
            await prompt_service.refresh_snapshot()
            prompt_service.mark_snapshot_source(source)
            _set_local_epoch(epoch)
            _last_refresh_at = now
            logger.info(
                "[PromptHotReload] refreshed epoch %s->%s source=%s",
                before, epoch, source,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — 旧快照继续服务
            logger.warning("[PromptHotReload] snapshot refresh failed: %s", exc)
            return False


async def _handle_reload_event(payload: dict[str, Any] | str) -> None:
    """解析 pub/sub 事件并触发快照刷新（供 listener 与测试共用）。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return
    try:
        epoch = int(payload.get("epoch", 0))
    except (AttributeError, TypeError, ValueError):
        return
    if epoch > local_epoch():
        await _refresh_if_newer(epoch, source="pubsub")


async def ensure_prompt_snapshot_fresh_async(*, force: bool = False) -> int:
    """请求/任务入口拉取检查；Redis 异常时保持旧快照并返回本地 epoch。"""
    global _last_epoch_check_at
    if not PROMPT_HOT_RELOAD_ENABLED:
        return local_epoch()
    now = time.monotonic()
    with _state_lock:
        if not force and now - _last_epoch_check_at < PROMPT_EPOCH_CHECK_TTL:
            return _local_epoch
        _last_epoch_check_at = now
    try:
        redis = get_redis()
        redis_epoch: int | None = None
        if redis is not None:
            raw = redis.get(PROMPT_EPOCH_KEY)
            if raw is not None:
                redis_epoch = int(raw)

        # 每个 TTL 窗口读取一次 DB，确保 Redis 在发布时完全不可用、或
        # 仅 INCR 未 PUBLISH 的情况下，恢复后仍能自动追平所有进程。
        db_epoch = await _read_db_epoch()
        authoritative = db_epoch if db_epoch is not None else redis_epoch
        if authoritative is None:
            return local_epoch()
        if redis is not None and db_epoch is not None and (
            redis_epoch is None or redis_epoch < db_epoch
        ):
            try:
                redis.set(PROMPT_EPOCH_KEY, db_epoch)
                redis.publish(
                    PROMPT_CHANGED_CHANNEL,
                    json.dumps({"epoch": db_epoch, "source": "db-recovery"}),
                )
            except Exception:
                logger.debug("[PromptHotReload] Redis epoch recovery publish failed", exc_info=True)
        if authoritative > local_epoch():
            await _refresh_if_newer(authoritative, source="poll")
        elif authoritative < local_epoch():
            logger.warning(
                "[PromptHotReload] redis epoch regressed %s<%s",
                authoritative, local_epoch(),
            )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.debug("[PromptHotReload] epoch check skipped: %s", exc)
    return local_epoch()


def ensure_prompt_snapshot_fresh(*, force: bool = False) -> int:
    """同步调用入口（worker/同步图）；事件循环内请使用 async 版本。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ensure_prompt_snapshot_fresh_async(force=force))
    # 同一事件循环不能嵌套 asyncio.run；异步请求入口使用 async 版本。
    return local_epoch()


def _next_pubsub_message(pubsub):
    """以非阻塞轮询读取消息，空闲时返回 ``None``。"""
    return pubsub.get_message(
        ignore_subscribe_messages=True,
        timeout=1.0,
    )


def _listener_loop() -> None:
    """Redis pub/sub daemon；断线后退避重连，重连后做一次拉检查。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while True:
            if not PROMPT_HOT_RELOAD_ENABLED:
                return
            pubsub = None
            try:
                redis = get_redis()
                if redis is None:
                    time.sleep(5.0)
                    continue
                pubsub = redis.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(PROMPT_CHANGED_CHANNEL)
                logger.info(
                    "[PromptHotReload] listener started channel=%s",
                    PROMPT_CHANGED_CHANNEL,
                )
                loop.run_until_complete(ensure_prompt_snapshot_fresh_async(force=True))
                # 不使用 ``PubSub.listen()``：底层客户端配置了有限的
                # socket_timeout，空闲期间会抛出读取超时，被外层误判为断线，
                # 造成每几秒重连一次。轮询接口把空闲超时视为正常状态，
                # 真正的连接异常仍由外层捕获并自愈。
                while True:
                    message = _next_pubsub_message(pubsub)
                    if not message:
                        continue
                    data = message.get("data") if isinstance(message, dict) else None
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", "replace")
                    if data:
                        loop.run_until_complete(_handle_reload_event(data))
            except Exception as exc:  # noqa: BLE001 — listener 自愈
                logger.warning("[PromptHotReload] listener reconnect: %s", exc)
                time.sleep(5.0)
            finally:
                try:
                    if pubsub is not None:
                        pubsub.close()
                except Exception:
                    pass
    finally:
        loop.close()


def start_prompt_reload_listener() -> None:
    """每进程启动一个 daemon listener；禁用开关时 no-op。"""
    global _listener_thread
    if not PROMPT_HOT_RELOAD_ENABLED:
        return
    _start_runtime_heartbeat()
    with _listener_lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            return
        _listener_thread = threading.Thread(
            target=_listener_loop,
            name="prompt-hot-reload",
            daemon=True,
        )
        _listener_thread.start()
