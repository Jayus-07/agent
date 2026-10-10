"""持久化 L3 提取 outbox 的 Celery worker 与恢复扫描器。

Celery 消息只携带 job UUID。对话正文只从 PostgreSQL 的会话消息表读取，
状态机以 memory_extraction_jobs 为准，broker/result backend 不作为事实源。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update

from backend.config.memory import (
    MEMORY_EXTRACTION_DISPATCH_COOLDOWN_SECONDS,
    MEMORY_EXTRACTION_MAX_ATTEMPTS,
    MEMORY_EXTRACTION_RECOVERY_BATCH_SIZE,
    MEMORY_EXTRACTION_STALE_SECONDS,
)
from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import MemoryExtractionJob
from backend.memory.models.session import ChatMessage, ChatSession
from backend.observability.metrics import (
    memory_extraction_duration_seconds,
    memory_extraction_job_total,
    memory_extraction_queue_backlog,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app
from backend.tasks.queue_router import log_route, resolve_for_celery_task


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _metric(outcome: str, amount: int = 1) -> None:
    """固定枚举指标；观测面异常不得影响 outbox 状态机。"""
    try:
        memory_extraction_job_total.labels(outcome=outcome).inc(amount)
    except Exception:  # pragma: no cover - metrics must not break queue processing
        pass


async def _refresh_backlog_gauges(db) -> None:
    try:
        rows = await db.execute(
            select(MemoryExtractionJob.status, func.count())
            .where(MemoryExtractionJob.status.in_(
                ("PENDING", "RUNNING", "FAILED"),
            ))
            .group_by(MemoryExtractionJob.status)
        )
        counts = {status: int(count) for status, count in rows.all()}
        for status in ("PENDING", "RUNNING", "FAILED"):
            memory_extraction_queue_backlog.labels(status=status.lower()).set(
                counts.get(status, 0),
            )
    except Exception:  # pragma: no cover - metrics must not break queue processing
        pass


def _publish(job_id: str, dispatch_type: str) -> None:
    """经统一队列路由投递；Celery 参数严格只包含 job UUID。"""
    route = resolve_for_celery_task("memory.extract")
    log_route(
        route,
        dispatch_type=dispatch_type,
        task_id=job_id,
        celery_task_name="memory.extract",
    )
    memory_extract_task.apply_async(args=[job_id], queue=route.physical_queue)


async def dispatch_memory_extraction(job_id: str) -> bool:
    """立即尝试投递一个新 outbox 项；失败后由 beat 按冷却周期补投。"""
    now = _now()
    cooldown = now - timedelta(seconds=MEMORY_EXTRACTION_DISPATCH_COOLDOWN_SECONDS)
    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(MemoryExtractionJob)
            .where(
                MemoryExtractionJob.id == UUID(job_id),
                MemoryExtractionJob.status == "PENDING",
                or_(MemoryExtractionJob.last_enqueued_at.is_(None),
                    MemoryExtractionJob.last_enqueued_at <= cooldown),
            )
            .with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if job is None:
            await db.rollback()
            return False
        job.last_enqueued_at = now
        job.updated_at = now
        await db.commit()

    # Broker I/O is synchronous; do not block MemoryManager's event loop.
    try:
        await asyncio.to_thread(_publish, job_id, "initial")
    except Exception:
        _metric("dispatch_failed")
        raise
    _metric("enqueued")
    return True


async def recover_memory_extractions() -> dict:
    """扫描可补投 PENDING / stale RUNNING jobs，并终结耗尽重试的僵尸任务。"""
    now = _now()
    stale_before = now - timedelta(seconds=MEMORY_EXTRACTION_STALE_SECONDS)
    cooldown_before = now - timedelta(seconds=MEMORY_EXTRACTION_DISPATCH_COOLDOWN_SECONDS)
    async with AsyncSessionLocal() as db:
        exhausted = await db.execute(
            update(MemoryExtractionJob)
            .where(
                MemoryExtractionJob.attempts >= MEMORY_EXTRACTION_MAX_ATTEMPTS,
                or_(
                    MemoryExtractionJob.status == "PENDING",
                    and_(MemoryExtractionJob.status == "RUNNING",
                         MemoryExtractionJob.claimed_at <= stale_before),
                ),
            )
            .values(status="FAILED", claimed_at=None,
                    last_error_code="MAX_ATTEMPTS", updated_at=now)
        )
        candidates = (await db.execute(
            select(MemoryExtractionJob)
            .where(
                MemoryExtractionJob.attempts < MEMORY_EXTRACTION_MAX_ATTEMPTS,
                or_(
                    and_(
                        MemoryExtractionJob.status == "PENDING",
                        or_(MemoryExtractionJob.last_enqueued_at.is_(None),
                            MemoryExtractionJob.last_enqueued_at <= cooldown_before),
                    ),
                    and_(
                        MemoryExtractionJob.status == "RUNNING",
                        MemoryExtractionJob.claimed_at <= stale_before,
                        or_(MemoryExtractionJob.last_enqueued_at.is_(None),
                            MemoryExtractionJob.last_enqueued_at <= cooldown_before),
                    ),
                ),
            )
            .order_by(MemoryExtractionJob.created_at)
            .limit(max(1, MEMORY_EXTRACTION_RECOVERY_BATCH_SIZE))
            .with_for_update(skip_locked=True)
        )).scalars().all()
        ids = [str(job.id) for job in candidates]
        for job in candidates:
            job.last_enqueued_at = now
            job.updated_at = now
        await db.flush()
        await _refresh_backlog_gauges(db)
        await db.commit()

    published = 0
    failed = 0
    for job_id in ids:
        try:
            await asyncio.to_thread(_publish, job_id, "recovery")
            published += 1
            _metric("recovered")
        except Exception as exc:
            # 预留时间戳避免 beat 热循环；冷却期后继续尝试。
            logger.warning(
                "[MemoryExtraction] recovery publish failed job=%s error=%s",
                job_id, type(exc).__name__,
            )
            failed += 1
            _metric("dispatch_failed")
    if exhausted.rowcount:
        _metric("exhausted", max(0, exhausted.rowcount))
        logger.warning("[MemoryExtraction] marked exhausted jobs failed count=%s",
                       exhausted.rowcount)
    return {"published": published, "publish_failed": failed,
            "exhausted": max(0, exhausted.rowcount or 0)}


async def _claim_job(job_id: UUID) -> tuple[dict | None, str]:
    now = _now()
    stale_before = now - timedelta(seconds=MEMORY_EXTRACTION_STALE_SECONDS)
    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(MemoryExtractionJob)
            .where(MemoryExtractionJob.id == job_id)
            .with_for_update()
        )).scalar_one_or_none()
        if job is None:
            await db.rollback()
            return None, "missing"
        if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            await db.rollback()
            return None, job.status.lower()
        if job.status == "RUNNING" and job.claimed_at and job.claimed_at > stale_before:
            await db.rollback()
            return None, "already_running"
        if job.attempts >= MEMORY_EXTRACTION_MAX_ATTEMPTS:
            job.status = "FAILED"
            job.claimed_at = None
            job.last_error_code = "MAX_ATTEMPTS"
            job.updated_at = now
            await db.commit()
            return None, "failed"

        job.status = "RUNNING"
        job.attempts += 1
        attempt = job.attempts
        job.claimed_at = now
        job.last_error_code = None
        job.updated_at = now

        owner = await db.scalar(
            select(ChatSession.user_id).where(ChatSession.session_id == job.session_id)
        )
        source = (await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == job.source_message_id,
                ChatMessage.session_id == job.session_id,
                ChatMessage.role == "user",
            )
        )).scalar_one_or_none()
        assistant = None
        if job.assistant_message_id is not None:
            assistant = (await db.execute(
                select(ChatMessage).where(
                    ChatMessage.id == job.assistant_message_id,
                    ChatMessage.session_id == job.session_id,
                    ChatMessage.role == "assistant",
                )
            )).scalar_one_or_none()
        if owner != job.user_id or source is None or assistant is None:
            job.status = "CANCELLED"
            job.claimed_at = None
            job.last_error_code = "SOURCE_UNAVAILABLE"
            job.updated_at = now
            await db.commit()
            return None, "cancelled"

        payload = {
            "job_id": str(job.id),
            "tenant_id": job.tenant_id,
            "user_id": job.user_id,
            "session_id": job.session_id,
            "source_message_id": job.source_message_id,
            "attempt": attempt,
            "question": source.content,
            "answer": assistant.content,
        }
        await db.commit()
        return payload, "claimed"


async def _heartbeat(job_id: UUID, attempt: int) -> None:
    interval = max(5, MEMORY_EXTRACTION_STALE_SECONDS // 3)
    while True:
        await asyncio.sleep(interval)
        now = _now()
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(MemoryExtractionJob)
                .where(
                    MemoryExtractionJob.id == job_id,
                    MemoryExtractionJob.status == "RUNNING",
                    MemoryExtractionJob.attempts == attempt,
                )
                .values(claimed_at=now, updated_at=now)
            )
            await db.commit()
            if result.rowcount != 1:
                return


async def _finish_job(job_id: UUID, attempt: int, result: dict) -> str:
    now = _now()
    status = result.get("status")
    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(MemoryExtractionJob)
            .where(
                MemoryExtractionJob.id == job_id,
                MemoryExtractionJob.status == "RUNNING",
                MemoryExtractionJob.attempts == attempt,
            )
            .with_for_update()
        )).scalar_one_or_none()
        if job is None:
            await db.rollback()
            return "superseded"
        if status == "succeeded":
            job.status = "SUCCEEDED"
            job.last_error_code = None
        elif status == "cancelled":
            job.status = "CANCELLED"
            job.last_error_code = "LEASE_LOST"
        elif job.attempts >= MEMORY_EXTRACTION_MAX_ATTEMPTS:
            job.status = "FAILED"
            job.last_error_code = str(result.get("error_code") or "EXTRACTION_FAILED")[:64]
        else:
            job.status = "PENDING"
            job.last_enqueued_at = now  # 有界退避：等待至少一个 dispatch cooldown
            job.last_error_code = str(result.get("error_code") or "EXTRACTION_FAILED")[:64]
        job.claimed_at = None
        job.updated_at = now
        final_status = job.status.lower()
        await db.commit()
        return final_status


async def process_memory_extraction(job_id_value: str) -> dict:
    started = time.perf_counter()
    try:
        job_id = UUID(str(job_id_value))
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid_job_id"}

    payload, claim_status = await _claim_job(job_id)
    if payload is None:
        return {"status": claim_status}

    heartbeat = asyncio.create_task(_heartbeat(job_id, payload["attempt"]))
    try:
        from backend.memory.service import MemoryService

        result = await MemoryService().store(
            payload["question"],
            payload["answer"],
            payload["session_id"],
            payload["user_id"],
            source_message_id=payload["source_message_id"],
            tenant_id=payload["tenant_id"],
            job_id=payload["job_id"],
            job_attempt=payload["attempt"],
        )
    except Exception as exc:
        logger.exception("[MemoryExtraction] worker failed job=%s error=%s",
                         job_id, type(exc).__name__)
        result = {"status": "failed", "error_code": "WORKER_ERROR"}
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)

    final_status = await _finish_job(job_id, payload["attempt"], result)
    metric_outcome = {
        "succeeded": "succeeded",
        "pending": "retry",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(final_status)
    if metric_outcome:
        _metric(metric_outcome)
    try:
        memory_extraction_duration_seconds.observe(time.perf_counter() - started)
    except Exception:  # pragma: no cover - metrics must not break queue processing
        pass
    return {"status": final_status, "facts": result.get("facts", 0),
            "stored": result.get("stored", 0)}


@celery_app.task(name="memory.extract")
def memory_extract_task(job_id: str) -> dict:
    """按 job UUID 提取并写入单轮 L3 记忆。"""
    return asyncio.run(process_memory_extraction(job_id))


@celery_app.task(name="memory.extraction_recovery")
def memory_extraction_recovery_task() -> dict:
    """恢复漏投消息、worker 崩溃和到达重试上限的 outbox 任务。"""
    return asyncio.run(recover_memory_extractions())
