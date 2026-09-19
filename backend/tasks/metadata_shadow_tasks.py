"""元数据影子评估 Celery 任务。

任务参数只允许 job_id；正文和主决策从影子任务表/缓存读取，避免把大文本
塞进 Redis broker。任务不调用 R2 LLM，只执行 shadow_route 的 L0-L2。
"""
from __future__ import annotations

import asyncio
import json
import time

from backend.config.tasks import (
    CELERY_METADATA_SHADOW_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.shared.logger import logger


def execute_metadata_shadow_task_impl(shadow_job_id: str) -> dict:
    """执行一个影子 job；异常上抛给 Celery 做有限退避重试。"""
    from backend.observability.metrics import (
        metadata_shadow_job_total,
        metadata_shadow_latency_seconds,
        metadata_shadow_queue_age_seconds,
    )
    from backend.rag.preprocessing import metadata_shadow

    job = metadata_shadow._load_shadow_job(shadow_job_id)
    if job is None:
        metadata_shadow_job_total.labels(result="skipped").inc()
        return {"status": "skipped", "reason": "job_not_found"}

    status = str(job.get("status") or "pending")
    if status in {"succeeded", "skipped"}:
        return {"status": status, "shadow_job_id": shadow_job_id}

    started = time.monotonic()
    attempts = int(job.get("attempts") or 0) + 1
    metadata_shadow._update_shadow_job(
        shadow_job_id,
        status="running",
        attempts=attempts,
        started_at="now",
        error="",
    )

    try:
        payload = metadata_shadow._load_shadow_input(job.get("input_cache_key", ""))
        if not payload:
            raise RuntimeError("shadow input cache miss")

        created_at = job.get("created_at")
        if created_at is not None:
            try:
                age = max(time.time() - created_at.timestamp(), 0.0)
                metadata_shadow_queue_age_seconds.observe(age)
            except (AttributeError, TypeError, ValueError):
                pass

        from backend.rag.embedding_singleton import get_embedding
        from backend.rag.preprocessing.metadata_router import shadow_route

        async def _run_shadow():
            from backend.rag.preprocessing.metadata_runtime import run_limited

            embedding = await asyncio.to_thread(get_embedding)
            return await run_limited(
                "shadow",
                lambda: shadow_route(
                    payload.get("text", ""),
                    payload.get("filename", ""),
                    payload.get("file_path", ""),
                    embedding=embedding,
                ),
            )

        decision = asyncio.run(_run_shadow())
        main_envelope = job.get("main_envelope_json") or {}
        if isinstance(main_envelope, str):
            main_envelope = json.loads(main_envelope)
        main_doc_type = str(main_envelope.get("doc_type", ""))
        shadow_doc_type = decision.doc_type if decision is not None else ""
        shadow_level = decision.level if decision is not None else ""
        agreement = bool(decision is not None and shadow_doc_type == main_doc_type)
        latency_ms = (time.monotonic() - started) * 1000
        from backend.observability.metrics import metadata_route_total

        metadata_route_total.labels(
            level=f"shadow_{shadow_level or 'none'}",
            outcome="agree" if agreement else "differ",
        ).inc()

        metadata_shadow._update_shadow_job(
            shadow_job_id,
            status="succeeded",
            finished_at="now",
            shadow_doc_type=shadow_doc_type,
            shadow_level=shadow_level,
            main_doc_type=main_doc_type,
            agreement=agreement,
            latency_ms=latency_ms,
        )
        metadata_shadow_latency_seconds.observe(max(latency_ms / 1000, 0.0))
        metadata_shadow_job_total.labels(result="succeeded").inc()
        return {
            "status": "succeeded",
            "shadow_job_id": shadow_job_id,
            "shadow_doc_type": shadow_doc_type,
            "shadow_level": shadow_level,
            "main_doc_type": main_doc_type,
            "agreement": agreement,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        metadata_shadow._update_shadow_job(
            shadow_job_id,
            status="failed",
            finished_at="now",
            error=str(exc)[:500],
        )
        metadata_shadow_job_total.labels(result="failed").inc()
        logger.warning(f"[MetaShadowTask] 影子任务失败: {shadow_job_id}: {exc}")
        raise


def _register_task():
    from celery.exceptions import SoftTimeLimitExceeded

    from backend.tasks.celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="tasks.execute_metadata_shadow",
        acks_late=True,
        autoretry_for=(Exception,),
        retry_backoff=CELERY_RETRY_BACKOFF,
        retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
        retry_jitter=True,
        max_retries=CELERY_METADATA_SHADOW_MAX_RETRIES,
    )
    def execute_metadata_shadow(self, shadow_job_id: str) -> dict:
        try:
            return execute_metadata_shadow_task_impl(shadow_job_id)
        except SoftTimeLimitExceeded:
            raise

    return execute_metadata_shadow


execute_metadata_shadow = _register_task()

__all__ = ["execute_metadata_shadow", "execute_metadata_shadow_task_impl"]
