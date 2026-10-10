"""L3 删除会清除正文/向量，并阻止删除前来源的记忆重放。"""

from __future__ import annotations

import hashlib
import uuid

import numpy as np
import pytest
from sqlalchemy import select, text

from backend.memory.database import AsyncSessionLocal
from backend.memory.keying import StoreOutcome
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.models.memory import EMBEDDING_DIM, MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.repository.session_repo import SessionRepository
from backend.tests.memory.conftest import require_memory_pg

pytestmark = pytest.mark.asyncio

_RUN_ID = uuid.uuid4().hex
_PREFIX = f"memdel-{_RUN_ID[:12]}"
_TENANT = f"t-{_RUN_ID[:10]}"
_USER = f"{_PREFIX}-user"
_SESSION = f"{_PREFIX}-session"


class _Embedding:
    def embed_query(self, text_value: str) -> list[float]:
        seed = int(hashlib.md5(text_value.encode("utf-8")).hexdigest(), 16)
        rng = np.random.default_rng(seed % (2**32))
        vector = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        return (vector / np.linalg.norm(vector)).tolist()


@pytest.fixture(autouse=True)
async def _isolated_postgres_data():
    require_memory_pg()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM memory_records WHERE user_id LIKE :prefix"),
            {"prefix": _PREFIX + "%"},
        )
        await db.execute(
            text("DELETE FROM chat_sessions WHERE session_id LIKE :prefix"),
            {"prefix": _PREFIX + "%"},
        )
        await db.commit()


async def _source_message(content: str) -> int:
    async with AsyncSessionLocal() as db:
        sessions = SessionRepository(db)
        await sessions.get_or_create(_SESSION, _USER)
        message = await sessions.save_message(_SESSION, "user", content)
        await db.commit()
        return message.id


def _fact(value: str, source_id: int) -> MemoryFact:
    return MemoryFact(
        fact_type="preference",
        content=f"酒店偏好：{value}",
        origin="inferred",
        confidence_score=0.7,
        source_message_id=source_id,
        memory_key="hotel.location",
        structured_value=value,
    )


async def test_delete_keyed_version_chain_clears_vectors_and_blocks_old_source():
    old_source = await _source_message("以后酒店优先靠近地铁")
    update_source = await _source_message("更正：以后优先住在市中心")
    l3 = LongTermMemory(None)
    l3._embedding_model = _Embedding()

    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        first = await l3.store_with_resolution(
            _fact("near_metro", old_source), _USER, _SESSION, _TENANT,
        )
        await db.commit()
        second = await l3.store_with_resolution(
            _fact("city_center", update_source), _USER, _SESSION, _TENANT,
        )
        await db.commit()
    assert first.outcome == StoreOutcome.INSERTED
    assert second.outcome == StoreOutcome.SUPERSEDED

    from backend.memory.service import MemoryService

    deleted = await MemoryService().delete_profile_memory(
        user_id=_USER, tenant_id=_TENANT, memory_key="hotel.location",
    )
    assert deleted["ok"] is True
    assert deleted["deleted_records"] == 2

    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(select(MemoryRecord).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
        ))).scalars().all())
    assert len(rows) == 1
    assert rows[0].memory_type == "tombstone"
    assert rows[0].content == ""
    assert rows[0].embedding is None
    assert rows[0].structured_value is None

    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        replay = await l3.store_with_resolution(
            _fact("near_metro", old_source), _USER, _SESSION, _TENANT,
        )
        await db.commit()
    assert replay.outcome == StoreOutcome.BLOCKED_BY_DELETE

    later_source = await _source_message("以后仍然想住在市中心附近")
    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        later = await l3.store_with_resolution(
            _fact("city_center", later_source), _USER, _SESSION, _TENANT,
        )
        await db.commit()
    assert later.outcome == StoreOutcome.INSERTED


async def test_delete_by_record_id_is_scoped_to_authenticated_owner_and_tenant():
    source = await _source_message("我喜欢安静的咖啡馆")
    l3 = LongTermMemory(None)
    l3._embedding_model = _Embedding()
    fact = MemoryFact(
        fact_type="preference",
        content="喜欢安静的咖啡馆",
        origin="explicit",
        source_message_id=source,
    )
    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        stored = await l3.store_with_resolution(fact, _USER, _SESSION, _TENANT)
        await db.commit()
        record = (await db.execute(select(MemoryRecord).where(
            MemoryRecord.id == uuid.UUID(stored.memory_id),
        ))).scalar_one()

    from backend.memory.service import MemoryService

    denied = await MemoryService().delete_profile_memory(
        user_id=_USER, tenant_id="another-tenant", memory_id=str(record.id),
    )
    assert denied == {"ok": False, "error": "记忆不存在"}

    allowed = await MemoryService().delete_profile_memory(
        user_id=_USER, tenant_id=_TENANT, memory_id=str(record.id),
    )
    assert allowed["ok"] is True


async def test_profile_projection_is_complete_when_records_list_is_limited():
    from backend.memory.service import MemoryService

    source = await _source_message("以后旅行轻松，酒店要安静")
    l3 = LongTermMemory(None)
    l3._embedding_model = _Embedding()
    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        for memory_key, value, content in (
            ("travel.pace", "relaxed", "旅行节奏偏轻松"),
            ("hotel.quiet", "true", "偏好安静酒店"),
        ):
            result = await l3.store_with_resolution(MemoryFact(
                fact_type="preference",
                content=content,
                origin="explicit",
                source_message_id=source,
                memory_key=memory_key,
                structured_value=value,
            ), _USER, _SESSION, _TENANT)
            assert result.outcome == StoreOutcome.INSERTED
        await db.commit()

    profile = await MemoryService().get_profile(
        user_id=_USER, tenant_id=_TENANT, limit=1,
    )
    assert profile["total"] == 2
    assert len(profile["records"]) == 1
    assert profile["has_more"] is True
    assert profile["profile"] == {
        "travel": {"pace": "relaxed"},
        "hotel": {"quiet": True},
    }

    async with AsyncSessionLocal() as db:
        repo = MemoryRepository(db)
        query_embedding = l3.embedding.embed_query("旅行节奏偏轻松")
        general_rows = await repo.search_hybrid(
            query_embedding, _USER, tenant_id=_TENANT, domain="general",
        )
        travel_rows = await repo.search_hybrid(
            query_embedding, _USER, tenant_id=_TENANT, domain="travel",
        )
    assert general_rows == []
    assert {record.memory_key for record, _ in travel_rows} == {
        "travel.pace", "hotel.quiet",
    }


async def test_inferred_memory_stays_pending_until_user_verifies_it():
    from backend.memory.service import MemoryService

    source = await _source_message("以后旅行节奏轻松一点")
    l3 = LongTermMemory(None)
    l3._embedding_model = _Embedding()
    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        stored = await l3.store_with_resolution(
            MemoryFact(
                fact_type="preference",
                content="旅行节奏偏轻松",
                origin="inferred",
                source_message_id=source,
                memory_key="travel.pace",
                structured_value="relaxed",
            ), _USER, _SESSION, _TENANT,
        )
        await db.commit()
        assert stored.outcome == StoreOutcome.INSERTED
        record_id = stored.memory_id

        invisible = await db.execute(select(MemoryRecord).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
            MemoryRecord.verification_status.in_(("verified", "legacy")),
        ))
        assert invisible.scalars().all() == []

    service = MemoryService()
    pending = await service.get_pending_profile(_USER, _TENANT)
    assert pending["total"] == 1
    assert pending["records"][0]["id"] == record_id
    assert pending["records"][0]["verification_status"] == "pending"

    verified = await service.verify_profile_memory(_USER, _TENANT, record_id)
    assert verified["ok"] is True
    active_profile = await service.get_profile(_USER, _TENANT)
    assert active_profile["profile"] == {"travel": {"pace": "relaxed"}}
    assert active_profile["records"][0]["verification_status"] == "verified"


async def test_late_old_source_cannot_supersede_a_newer_inferred_version():
    old_source = await _source_message("喜欢轻松节奏")
    new_source = await _source_message("更正：以后喜欢紧凑节奏")
    l3 = LongTermMemory(None)
    l3._embedding_model = _Embedding()

    async with AsyncSessionLocal() as db:
        l3._repo = MemoryRepository(db)
        newest = await l3.store_with_resolution(MemoryFact(
            fact_type="preference", content="旅行偏好紧凑节奏",
            origin="inferred", source_message_id=new_source,
            memory_key="travel.pace", structured_value="packed",
        ), _USER, _SESSION, _TENANT)
        await db.commit()
        late = await l3.store_with_resolution(MemoryFact(
            fact_type="preference", content="旅行偏好轻松节奏",
            origin="inferred", source_message_id=old_source,
            memory_key="travel.pace", structured_value="relaxed",
        ), _USER, _SESSION, _TENANT)
        await db.commit()

    assert newest.outcome == StoreOutcome.INSERTED
    assert late.outcome == StoreOutcome.BLOCKED_STALE_EVENT
    async with AsyncSessionLocal() as db:
        active = (await db.execute(select(MemoryRecord).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
            MemoryRecord.memory_key == "travel.pace",
            MemoryRecord.is_active.is_(True),
        ))).scalar_one()
    assert active.structured_value == "packed"
    assert active.version == 1
