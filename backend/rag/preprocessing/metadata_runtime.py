"""元数据管道的有界资源、缓存和幂等控制。"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypeVar

from backend.infra.cache import get_cache
from backend.observability.metrics import (
    metadata_cache_total,
    metadata_queue_wait_seconds,
    metadata_resource_active,
    metadata_resource_queued,
    metadata_resource_wait_timeout_total,
)
from backend.rag.preprocessing.metadata_schema import DecisionEnvelope
from backend.shared.logger import logger


T = TypeVar("T")
MetadataResource = Literal["embedding", "llm", "db", "shadow"]


class MetadataResourceTimeout(TimeoutError):
    """等待元数据资源槽位超时。"""

    def __init__(self, resource: str, timeout: float):
        super().__init__(f"metadata resource {resource} wait timeout after {timeout}s")
        self.resource = resource
        self.timeout = timeout


@dataclass
class MetadataLimiters:
    embedding: asyncio.Semaphore
    llm: asyncio.Semaphore
    db: asyncio.Semaphore
    shadow: asyncio.Semaphore


_limiters_by_loop: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, tuple[tuple[int, int, int, int], MetadataLimiters]
] = weakref.WeakKeyDictionary()
_limiters_lock = threading.Lock()
_metadata_cache = None
_metadata_cache_lock = threading.Lock()


def _configured_widths() -> tuple[int, int, int, int]:
    from backend.config import rag

    return (
        max(int(getattr(rag, "METADATA_EMBED_CONCURRENCY", 4)), 1),
        max(int(getattr(rag, "METADATA_LLM_CONCURRENCY", 8)), 1),
        max(int(getattr(rag, "METADATA_DB_CONCURRENCY", 16)), 1),
        max(int(getattr(rag, "METADATA_SHADOW_CONCURRENCY", 2)), 1),
    )


def get_metadata_limiters() -> MetadataLimiters:
    """按当前事件循环返回独立信号量集合。"""
    loop = asyncio.get_running_loop()
    widths = _configured_widths()
    with _limiters_lock:
        current = _limiters_by_loop.get(loop)
        if current is not None and current[0] == widths:
            return current[1]
        limiters = MetadataLimiters(
            embedding=asyncio.Semaphore(widths[0]),
            llm=asyncio.Semaphore(widths[1]),
            db=asyncio.Semaphore(widths[2]),
            shadow=asyncio.Semaphore(widths[3]),
        )
        _limiters_by_loop[loop] = (widths, limiters)
        return limiters


def _get_semaphore(resource: str) -> asyncio.Semaphore:
    if resource not in {"embedding", "llm", "db", "shadow"}:
        raise ValueError(f"unknown metadata resource: {resource}")
    return getattr(get_metadata_limiters(), resource)


def _default_wait_timeout() -> float:
    from backend.config.rag import METADATA_RESOURCE_WAIT_TIMEOUT

    return float(METADATA_RESOURCE_WAIT_TIMEOUT)


@asynccontextmanager
async def with_metadata_limit(resource: MetadataResource, timeout: float | None = None):
    """获取资源槽位；等待超时以 typed exception 交给上层安全降级。"""
    semaphore = _get_semaphore(resource)
    wait_timeout = _default_wait_timeout() if timeout is None else float(timeout)
    queued = metadata_resource_queued.labels(resource=resource)
    active = metadata_resource_active.labels(resource=resource)
    queued.inc()
    started = time.monotonic()
    try:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=wait_timeout)
        except asyncio.TimeoutError as exc:
            metadata_resource_wait_timeout_total.labels(resource=resource).inc()
            raise MetadataResourceTimeout(resource, wait_timeout) from exc
        waited = time.monotonic() - started
        metadata_queue_wait_seconds.labels(resource=resource).observe(waited)
        active.inc()
    finally:
        queued.dec()
    try:
        yield
    finally:
        semaphore.release()
        active.dec()


async def run_limited(
    resource: MetadataResource,
    operation: Callable[[], Awaitable[T]],
    timeout: float | None = None,
) -> T:
    async with with_metadata_limit(resource, timeout):
        result = operation()
        if inspect.isawaitable(result):
            return await result
        return result  # type: ignore[return-value]


async def get_cached_decision_async(
    key: str,
    *,
    timeout: float = 0.1,
) -> DecisionEnvelope | None:
    """在线程池中读取缓存，避免同步 Redis I/O 占住事件循环。

    缓存只是优化层，槽位等待或后端异常都按软 miss 处理；主索引不能
    因缓存故障失败。
    """
    try:
        return await run_limited(
            "db",
            lambda: asyncio.to_thread(get_cached_decision, key),
            timeout=timeout,
        )
    except MetadataResourceTimeout:
        metadata_cache_total.labels(result="timeout").inc()
        return None
    except Exception as exc:
        metadata_cache_total.labels(result="error").inc()
        logger.debug(f"[MetaRuntime] async metadata cache read failed: {exc}")
        return None


async def put_cached_decision_async(
    key: str,
    envelope: DecisionEnvelope,
    *,
    allow_abstain: bool = False,
    timeout: float = 0.1,
) -> bool:
    """在线程池中写缓存；缓存拥塞不得阻塞或失败主索引。"""
    if not allow_abstain and envelope.decision != "accepted":
        return False
    try:
        return bool(
            await run_limited(
                "db",
                lambda: asyncio.to_thread(
                    put_cached_decision,
                    key,
                    envelope,
                    allow_abstain=allow_abstain,
                ),
                timeout=timeout,
            )
        )
    except MetadataResourceTimeout:
        metadata_cache_total.labels(result="timeout").inc()
        return False
    except Exception as exc:
        metadata_cache_total.labels(result="error").inc()
        logger.debug(f"[MetaRuntime] async metadata cache write failed: {exc}")
        return False


def metadata_cache_key(
    text: str,
    filename: str,
    file_path: str,
    taxonomy_version: str,
    model_version: str,
    prompt_version: str | int,
    rules_version: str = "",
) -> str:
    """生成跨进程稳定的版本化决策缓存键。"""
    payload = {
        "text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        "filename": (filename or "").strip().lower(),
        "file_path": (file_path or "").replace("\\", "/").strip().lower(),
        "taxonomy_version": str(taxonomy_version),
        "rules_version": str(rules_version),
        "model_version": str(model_version),
        "prompt_version": str(prompt_version),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"metadata:decision:{digest}"


def idempotency_key(
    file_hash: str,
    taxonomy_version: str,
    rules_version: str,
    model_version: str,
) -> str:
    payload = {
        "file_hash": str(file_hash),
        "taxonomy_version": str(taxonomy_version),
        "rules_version": str(rules_version),
        "model_version": str(model_version),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"metadata:idempotency:{digest}"


def _cache():
    global _metadata_cache
    if _metadata_cache is not None:
        return _metadata_cache
    with _metadata_cache_lock:
        if _metadata_cache is None:
            from backend.config.rag import METADATA_CACHE_TTL_SECONDS

            _metadata_cache = get_cache("rag_metadata", ttl=int(METADATA_CACHE_TTL_SECONDS))
    return _metadata_cache


def get_cached_decision(key: str) -> DecisionEnvelope | None:
    try:
        payload = _cache().get_json(key)
        if payload is None:
            metadata_cache_total.labels(result="miss").inc()
            return None
        envelope = DecisionEnvelope.model_validate(payload)
        metadata_cache_total.labels(result="hit").inc()
        return envelope
    except Exception as exc:
        metadata_cache_total.labels(result="error").inc()
        logger.debug(f"[MetaRuntime] metadata cache read failed: {exc}")
        return None


def put_cached_decision(
    key: str,
    envelope: DecisionEnvelope,
    *,
    allow_abstain: bool = False,
) -> bool:
    if not allow_abstain and envelope.decision != "accepted":
        return False
    try:
        from backend.config.rag import METADATA_CACHE_TTL_SECONDS

        _cache().set_json(
            key,
            envelope.model_dump(mode="json"),
            ttl=int(METADATA_CACHE_TTL_SECONDS),
        )
        metadata_cache_total.labels(result="write").inc()
        return True
    except Exception as exc:
        metadata_cache_total.labels(result="error").inc()
        logger.debug(f"[MetaRuntime] metadata cache write failed: {exc}")
        return False
