"""元数据影子评估的输入暂存与有界投递。

影子链路只接收 job id，正文保存在短 TTL 缓存中；Celery 投递失败、缓存
不可用或队列达到并发上限都只会跳过影子，不影响主索引结果。
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

from backend.infra.cache import get_cache
from backend.observability.metrics import (
    metadata_shadow_dispatch_total,
    metadata_shadow_job_total,
)
from backend.rag.preprocessing.metadata_schema import DecisionEnvelope
from backend.shared.logger import logger


_shadow_cache = None
_shadow_cache_lock = threading.Lock()


def _sample_text(text: str, max_chars: int) -> str:
    """按头尾采样，严格保证缓存正文不超过上限。"""
    limit = max(int(max_chars), 1)
    if len(text) <= limit:
        return text
    head = max(int(limit * 0.6), 1)
    tail = limit - head
    return text[:head] + (text[-tail:] if tail else "")


def _get_shadow_cache():
    global _shadow_cache
    if _shadow_cache is not None:
        return _shadow_cache
    with _shadow_cache_lock:
        if _shadow_cache is None:
            from backend.config.rag import METADATA_IDEMPOTENCY_TTL_SECONDS

            _shadow_cache = get_cache(
                "rag_metadata_shadow",
                ttl=int(METADATA_IDEMPOTENCY_TTL_SECONDS),
            )
    return _shadow_cache


def _input_cache_key(job_id: str) -> str:
    return f"metadata:shadow:input:{job_id}"


def _input_ttl() -> int:
    from backend.config.rag import METADATA_IDEMPOTENCY_TTL_SECONDS

    return int(METADATA_IDEMPOTENCY_TTL_SECONDS)


def _connect():
    import psycopg2

    from backend.config.database import RAG_STORES_PG_CONFIG

    return psycopg2.connect(**RAG_STORES_PG_CONFIG, connect_timeout=3)


def _write_shadow_job(
    *,
    envelope: DecisionEnvelope,
    text: str,
    filename: str,
    file_path: str,
) -> str:
    """暂存输入并写入影子任务行；数据库约束负责重复任务去重。"""
    from backend.config.rag import METADATA_LLM_EXTRACT_MAX_CHARS

    job_id = str(uuid.uuid4())
    sampled_text = _sample_text(text, METADATA_LLM_EXTRACT_MAX_CHARS)
    input_cache_key = _input_cache_key(job_id)
    payload = {
        "text": sampled_text,
        "filename": filename or "",
        "file_path": file_path or "",
    }
    _get_shadow_cache().set_json(
        input_cache_key,
        payload,
        ttl=_input_ttl(),
    )

    text_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    envelope_json = envelope.model_dump(mode="json")
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai.metadata_shadow_jobs (
                    id, upload_id, doc_id, text_hash, filename, file_path,
                    main_envelope_json, input_cache_key, status, attempts,
                    taxonomy_version, rules_version, model_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'pending', 0,
                          %s, %s, %s)
                ON CONFLICT (text_hash, taxonomy_version, rules_version, model_version)
                DO UPDATE SET updated_at = now()
                RETURNING id
                """,
                (
                    job_id,
                    str(envelope_json.get("upload_id", "")),
                    str(envelope_json.get("doc_id", "")),
                    text_hash,
                    filename or "",
                    file_path or "",
                    json.dumps(envelope_json, ensure_ascii=False),
                    input_cache_key,
                    envelope.taxonomy_version,
                    envelope.rules_version,
                    envelope.model_version,
                ),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("metadata shadow job insert returned no id")
            selected_id = str(row[0])
            if selected_id != job_id:
                selected_key = _input_cache_key(selected_id)
                _get_shadow_cache().set_json(
                    selected_key,
                    payload,
                    ttl=_input_ttl(),
                )
                cursor.execute(
                    """
                    UPDATE ai.metadata_shadow_jobs
                    SET input_cache_key = %s, updated_at = now()
                    WHERE id = %s AND status IN ('pending', 'skipped')
                    """,
                    (selected_key, selected_id),
                )
            return selected_id


def _load_shadow_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM ai.metadata_shadow_jobs WHERE id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))


def _update_shadow_job(job_id: str, **fields: Any) -> None:
    allowed = {
        "status", "attempts", "error", "started_at", "finished_at",
        "shadow_doc_type", "shadow_level", "main_doc_type", "agreement",
        "latency_ms",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    assignments_parts: list[str] = []
    values: list[Any] = []
    for key, value in updates.items():
        if value == "now" and key in {"started_at", "finished_at"}:
            assignments_parts.append(f"{key} = now()")
        else:
            assignments_parts.append(f"{key} = %s")
            values.append(value)
    assignments = ", ".join(assignments_parts)
    values.append(job_id)
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE ai.metadata_shadow_jobs SET {assignments}, updated_at = now() WHERE id = %s",
                values,
            )


def _load_shadow_input(cache_key: str) -> dict[str, Any] | None:
    payload = _get_shadow_cache().get_json(cache_key)
    return payload if isinstance(payload, dict) else None


def _enqueue_shadow_task(job_id: str) -> bool:
    from backend.config.tasks import CELERY_METADATA_SHADOW_QUEUE
    from backend.tasks.metadata_shadow_tasks import execute_metadata_shadow

    execute_metadata_shadow.apply_async(
        args=[job_id],
        queue=CELERY_METADATA_SHADOW_QUEUE,
    )
    return True


class _BoundedShadowDispatcher:
    """限制同时向 broker 发起的影子投递数，避免故障时线程无限堆积。"""

    def __init__(self) -> None:
        from backend.config.rag import METADATA_SHADOW_CONCURRENCY

        width = max(int(METADATA_SHADOW_CONCURRENCY), 1)
        self._slots = threading.BoundedSemaphore(width)

    def submit(self, job_id: str, payload: dict[str, Any]) -> bool:
        del payload
        if not self._slots.acquire(blocking=False):
            return False
        try:
            return bool(_enqueue_shadow_task(job_id))
        finally:
            self._slots.release()


_shadow_dispatcher = _BoundedShadowDispatcher()


def _mark_shadow_skipped_safely(job_id: str, reason: str) -> None:
    try:
        _update_shadow_job(job_id, status="skipped", error=reason, finished_at="now")
    except Exception as exc:
        logger.debug(f"[MetaShadow] 标记 skipped 失败: {exc}")


def submit_shadow_job(
    main_envelope: DecisionEnvelope,
    text: str,
    filename: str,
    file_path: str,
) -> str | None:
    """写入影子任务并尝试投递；任何失败都返回 None。"""
    try:
        job_id = _write_shadow_job(
            envelope=main_envelope,
            text=text,
            filename=filename,
            file_path=file_path,
        )
        from backend.config.rag import METADATA_LLM_EXTRACT_MAX_CHARS

        payload = {
            "job_id": job_id,
            "text": _sample_text(text, METADATA_LLM_EXTRACT_MAX_CHARS),
            "filename": filename or "",
            "file_path": file_path or "",
        }
        if not _shadow_dispatcher.submit(job_id, payload):
            _mark_shadow_skipped_safely(job_id, "shadow dispatcher full")
            metadata_shadow_dispatch_total.labels(result="skipped").inc()
            metadata_shadow_job_total.labels(result="skipped").inc()
            return None
        metadata_shadow_dispatch_total.labels(result="submitted").inc()
        return job_id
    except Exception as exc:
        metadata_shadow_dispatch_total.labels(result="failed").inc()
        metadata_shadow_job_total.labels(result="failed").inc()
        logger.warning(f"[MetaShadow] 影子任务投递失败（不影响主索引）: {exc}")
        return None


__all__ = [
    "submit_shadow_job",
    "_load_shadow_job",
    "_load_shadow_input",
    "_update_shadow_job",
    "_sample_text",
]
