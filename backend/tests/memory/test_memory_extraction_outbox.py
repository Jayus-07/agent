"""L3 durable outbox：同事务建单、ID-only dispatch、租约恢复与删除取消。"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import MemoryExtractionJob, MemoryTurnReceipt
from backend.memory.models.session import ChatMessage, ChatSession
from backend.memory.repository.session_repo import SessionRepository
from backend.tests.memory.conftest import require_memory_pg

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:12]
_PREFIX = f"memjob-{_RUN}"
_TENANT = f"tenant-{_RUN}"
_USER = f"{_PREFIX}-user"
_SESSION = f"{_PREFIX}-session"


@pytest.fixture(autouse=True)
async def _isolated_postgres_data():
    require_memory_pg()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            "DELETE FROM memory_extraction_jobs WHERE user_id LIKE :prefix"),
            {"prefix": _PREFIX + "%"},
        )
        await db.execute(text(
            "DELETE FROM memory_records WHERE user_id LIKE :prefix"),
            {"prefix": _PREFIX + "%"},
        )
        await db.execute(text(
            "DELETE FROM chat_sessions WHERE session_id LIKE :prefix"),
            {"prefix": _PREFIX + "%"},
        )
        await db.commit()


async def _create_turn(session_id: str = _SESSION) -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(session_id, _USER, _TENANT)
        user_message, assistant_message = await repo.save_turn(
            session_id, "我平时喜欢安静的酒店", "收到，我会记住。",
        )
        await db.commit()
        return user_message.id, assistant_message.id


async def _create_job(session_id: str = _SESSION) -> uuid.UUID:
    user_message_id, assistant_message_id = await _create_turn(session_id)
    async with AsyncSessionLocal() as db:
        job = MemoryExtractionJob(
            tenant_id=_TENANT,
            user_id=_USER,
            session_id=session_id,
            source_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )
        db.add(job)
        await db.flush()
        job_id = job.id
        await db.commit()
        return job_id


async def _require_turn_receipt_schema() -> None:
    async with AsyncSessionLocal() as db:
        exists = (await db.execute(
            text("SELECT to_regclass('public.memory_turn_idempotency')")
        )).scalar_one_or_none()
    if exists is None:
        pytest.skip("090_memory_turn_idempotency.sql 未应用，跳过真实轮次幂等验收")


async def test_end_turn_commits_outbox_with_messages_and_dispatches_only_job_id(monkeypatch):
    from backend.memory.service import MemoryService
    from backend.tasks import memory_extraction_tasks

    published: list[str] = []

    async def record_dispatch(job_id: str) -> bool:
        published.append(job_id)
        return True

    async def no_summary(self, session_id: str) -> None:
        return None

    monkeypatch.setattr(memory_extraction_tasks, "dispatch_memory_extraction", record_dispatch)
    monkeypatch.setattr(MemoryService, "_summarize_if_needed", no_summary)

    await MemoryService().end_turn(
        _SESSION, "我平时喜欢安静的酒店", "收到，我会记住。",
        user_id=_USER, tenant_id=_TENANT,
    )

    async with AsyncSessionLocal() as db:
        messages = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == _SESSION)
            .order_by(ChatMessage.id)
        )).scalars().all()
        jobs = (await db.execute(
            select(MemoryExtractionJob).where(
                MemoryExtractionJob.user_id == _USER,
                MemoryExtractionJob.session_id == _SESSION,
            )
        )).scalars().all()

    assert [message.role for message in messages] == ["user", "assistant"]
    assert len(jobs) == 1
    assert jobs[0].source_message_id == messages[0].id
    assert jobs[0].assistant_message_id == messages[1].id
    assert jobs[0].status == "PENDING"
    assert published == [str(jobs[0].id)]


async def test_end_turn_idempotency_is_atomic_and_scoped(monkeypatch):
    from backend.memory.service import (
        MemoryService,
        MemoryTurnIdempotencyConflict,
    )
    from backend.tasks import memory_extraction_tasks

    await _require_turn_receipt_schema()
    async with AsyncSessionLocal() as db:
        await SessionRepository(db).get_or_create(_SESSION, _USER, _TENANT)
        await db.commit()

    published: list[str] = []

    async def record_dispatch(job_id: str) -> bool:
        published.append(job_id)
        return True

    async def no_summary(self, session_id: str) -> None:
        return None

    monkeypatch.setattr(memory_extraction_tasks, "dispatch_memory_extraction", record_dispatch)
    monkeypatch.setattr(MemoryService, "_summarize_if_needed", no_summary)
    service = MemoryService()

    await asyncio.gather(
        service.end_turn(
            _SESSION, "稳定请求", "回答甲", user_id=_USER, tenant_id=_TENANT,
            idempotency_key="request-turn-1",
        ),
        service.end_turn(
            _SESSION, "稳定请求", "回答乙", user_id=_USER, tenant_id=_TENANT,
            idempotency_key="request-turn-1",
        ),
    )
    await service.end_turn(
        _SESSION, "稳定请求", "重试生成的新回答", user_id=_USER,
        tenant_id=_TENANT, idempotency_key="request-turn-1",
    )

    with pytest.raises(MemoryTurnIdempotencyConflict):
        await service.end_turn(
            _SESSION, "同键不同问题", "不得写入", user_id=_USER,
            tenant_id=_TENANT, idempotency_key="request-turn-1",
        )

    # 相同键在不同会话、不同用户下各自独立；用户复用他人 session 时仍隔离存储。
    other_session = f"{_PREFIX}-other-session"
    await service.end_turn(
        other_session, "稳定请求", "另一会话回答", user_id=_USER,
        tenant_id=_TENANT, idempotency_key="request-turn-1",
    )
    other_user = f"{_PREFIX}-other-user"
    await service.end_turn(
        _SESSION, "稳定请求", "另一用户回答", user_id=other_user,
        tenant_id=_TENANT, idempotency_key="request-turn-1",
    )

    async with AsyncSessionLocal() as db:
        messages = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == _SESSION)
            .order_by(ChatMessage.id)
        )).scalars().all()
        other_session_messages = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == other_session)
            .order_by(ChatMessage.id)
        )).scalars().all()
        other_user_sessions = (await db.execute(
            select(ChatSession.session_id).where(ChatSession.user_id == other_user)
        )).scalars().all()
        other_user_messages = (await db.execute(
            select(ChatMessage)
            .join(ChatSession, ChatSession.session_id == ChatMessage.session_id)
            .where(ChatSession.user_id == other_user)
            .order_by(ChatMessage.id)
        )).scalars().all()
        receipts = (await db.execute(
            select(MemoryTurnReceipt).where(
                MemoryTurnReceipt.tenant_id == _TENANT,
                MemoryTurnReceipt.user_id == _USER,
                MemoryTurnReceipt.session_id == _SESSION,
            )
        )).scalars().all()
        jobs = (await db.execute(
            select(MemoryExtractionJob).where(
                MemoryExtractionJob.tenant_id == _TENANT,
                MemoryExtractionJob.user_id == _USER,
                MemoryExtractionJob.session_id == _SESSION,
            )
        )).scalars().all()
        other_session_jobs = (await db.execute(
            select(MemoryExtractionJob).where(
                MemoryExtractionJob.tenant_id == _TENANT,
                MemoryExtractionJob.user_id == _USER,
                MemoryExtractionJob.session_id == other_session,
            )
        )).scalars().all()
        other_user_jobs = (await db.execute(
            select(MemoryExtractionJob).where(
                MemoryExtractionJob.tenant_id == _TENANT,
                MemoryExtractionJob.user_id == other_user,
            )
        )).scalars().all()

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "稳定请求"
    assert messages[1].content in {"回答甲", "回答乙"}
    assert len(receipts) == 1
    assert receipts[0].user_message_id == messages[0].id
    assert receipts[0].assistant_message_id == messages[1].id
    assert len(jobs) == 1
    assert len(other_session_jobs) == 1
    assert len(other_user_jobs) == 1
    assert len(published) == 5
    assert len(set(published[:3])) == 1
    assert len(set(published)) == 3
    assert [message.role for message in other_session_messages] == ["user", "assistant"]
    assert other_session_messages[1].content == "另一会话回答"
    assert len(other_user_sessions) == 1
    assert other_user_sessions[0] != _SESSION
    assert [message.role for message in other_user_messages] == ["user", "assistant"]
    assert other_user_messages[1].content == "另一用户回答"


async def test_worker_retries_failures_and_finishes_success(monkeypatch):
    from backend.memory.service import MemoryService
    from backend.tasks.memory_extraction_tasks import process_memory_extraction

    job_id = await _create_job()
    calls: list[dict] = []
    outcomes = iter((
        {"status": "failed", "error_code": "EXTRACTION_ERROR"},
        {"status": "failed", "error_code": "STORE_ERROR"},
        {"status": "succeeded", "facts": 1, "stored": 1},
    ))

    async def fake_store(self, question, answer, session_id, user_id="default", **kwargs):
        calls.append({"question": question, "answer": answer, **kwargs})
        return next(outcomes)

    monkeypatch.setattr(MemoryService, "store", fake_store)

    assert (await process_memory_extraction(str(job_id)))["status"] == "pending"
    assert (await process_memory_extraction(str(job_id)))["status"] == "pending"
    assert (await process_memory_extraction(str(job_id)))["status"] == "succeeded"
    async with AsyncSessionLocal() as db:
        job = await db.get(MemoryExtractionJob, job_id)
    assert job.status == "SUCCEEDED"
    assert job.attempts == 3
    assert calls[0]["question"] == "我平时喜欢安静的酒店"
    assert calls[0]["source_message_id"] > 0
    assert calls[0]["job_attempt"] == 1
    assert calls[0]["job_id"] == str(job_id)


async def test_recovery_requeues_due_pending_jobs_with_uuid_only(monkeypatch):
    import backend.tasks.memory_extraction_tasks as tasks

    job_id = await _create_job()
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "_publish", lambda job, kind: published.append((job, kind)))

    result = await tasks.recover_memory_extractions()

    assert result["published"] == 1
    assert published == [(str(job_id), "recovery")]
    async with AsyncSessionLocal() as db:
        job = await db.get(MemoryExtractionJob, job_id)
    assert job.status == "PENDING"
    assert job.last_enqueued_at is not None


async def test_deleting_session_cancels_pending_and_running_extractions():
    from backend.memory.service import MemoryService

    job_id = await _create_job()
    async with AsyncSessionLocal() as db:
        job = await db.get(MemoryExtractionJob, job_id)
        job.status = "RUNNING"
        job.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    result = await MemoryService().delete_session(
        _SESSION, user_id=_USER, tenant_id=_TENANT,
    )

    assert result["ok"] is True
    async with AsyncSessionLocal() as db:
        job = await db.get(MemoryExtractionJob, job_id)
    assert job.status == "CANCELLED"
    assert job.last_error_code == "SESSION_DELETED"
