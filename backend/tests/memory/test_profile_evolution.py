"""以固定 7 个月事件时间轴回归画像新增、更新、临时状态隔离和删除。"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy import delete, select, update

from backend.memory.database import AsyncSessionLocal
from backend.memory.keying import StoreOutcome
from backend.memory.long_term import LongTermMemory, MemoryFact
from backend.memory.models.memory import EMBEDDING_DIM, MemoryRecord
from backend.memory.models.session import ChatMessage, ChatSession
from backend.memory.repository.memory_repo import MemoryRepository
from backend.memory.repository.session_repo import SessionRepository
from backend.tests.memory.conftest import require_memory_pg

pytestmark = pytest.mark.asyncio

_RUN = uuid.uuid4().hex[:12]
_USER = f"memory-evolution-{_RUN}"
_TENANT = f"tenant-{_RUN}"
_MONTH1_SESSION = f"memory-evolution-{_RUN}-month1"
_MONTH7_SESSION = f"memory-evolution-{_RUN}-month7"
_SESSIONS = (_MONTH1_SESSION, _MONTH7_SESSION)


class _Embedding:
    def embed_query(self, text_value: str) -> list[float]:
        seed = int(hashlib.md5(text_value.encode("utf-8")).hexdigest(), 16)
        rng = np.random.default_rng(seed % (2**32))
        vector = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        return (vector / np.linalg.norm(vector)).tolist()


@pytest.fixture(autouse=True)
async def _isolated_profile_data():
    require_memory_pg()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(MemoryRecord).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
        ))
        await db.execute(delete(ChatMessage).where(
            ChatMessage.session_id.in_(_SESSIONS),
        ))
        await db.execute(delete(ChatSession).where(
            ChatSession.session_id.in_(_SESSIONS),
            ChatSession.user_id == _USER,
        ))
        await db.commit()


async def _source_message(text_value: str, month: int) -> int:
    """把事件固定到 2026 年对应月份；不等待真实时间流逝。"""
    session_id = _MONTH1_SESSION
    event_time = datetime(2026, month, 1, 12, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        await repo.get_or_create(session_id, _USER)
        message = await repo.save_message(session_id, "user", text_value)
        # 现存 chat_messages 时间列为 timestamp without time zone，保存 UTC 墙钟值。
        await db.execute(update(ChatMessage).where(
            ChatMessage.id == message.id,
        ).values(created_at=event_time.replace(tzinfo=None)))
        await db.commit()
        return message.id


async def _store(
    *, key: str, value: str, content: str, source_id: int,
    origin: str = "explicit",
):
    async with AsyncSessionLocal() as db:
        memory = LongTermMemory(MemoryRepository(db))
        memory._embedding_model = _Embedding()
        result = await memory.store_with_resolution(MemoryFact(
            fact_type="preference",
            content=content,
            origin=origin,
            confidence_score=0.95 if origin == "explicit" else 0.7,
            source_message_id=source_id,
            memory_key=key,
            structured_value=value,
        ), _USER, _MONTH1_SESSION, _TENANT)
        await db.commit()
        return result


async def test_six_month_profile_evolution_and_month_seven_cross_session_recall():
    # 月 1：明确的长期偏好进入 L3。
    month1 = await _source_message("以后旅行节奏轻松，酒店要安静", 1)
    pace_v1 = await _store(
        key="travel.pace", value="relaxed", content="旅行节奏偏轻松",
        source_id=month1,
    )
    quiet = await _store(
        key="hotel.quiet", value="true", content="偏好安静酒店",
        source_id=month1,
    )
    assert pace_v1.outcome == quiet.outcome == StoreOutcome.INSERTED

    # 月 2：目的地和预算只写入对话历史，不进入长期事实表。
    month2 = await _source_message("这次杭州，预算 3000 元", 2)

    # 月 3：新增长期交通偏好。
    month3 = await _source_message("以后酒店尽量靠近地铁", 3)
    location = await _store(
        key="hotel.location", value="near_metro", content="酒店靠近地铁",
        source_id=month3,
    )
    assert location.outcome == StoreOutcome.INSERTED

    # 月 4：本次苏州行程节奏紧凑，只保留在会话历史。
    month4 = await _source_message("这次苏州安排紧凑一点", 4)

    # 月 5：用户明确更新长期偏好，旧事实形成版本链。
    month5 = await _source_message("以后行程可以安排得中等紧凑", 5)
    pace_v2 = await _store(
        key="travel.pace", value="moderately_packed",
        content="旅行节奏偏中等紧凑", source_id=month5,
    )
    assert pace_v2.outcome == StoreOutcome.SUPERSEDED

    # 月 4 的旧异步推断在月 5 后完成，不能覆盖当前显式偏好。
    stale = await _store(
        key="travel.pace", value="packed", content="旅行节奏偏紧凑",
        source_id=month4, origin="inferred",
    )
    assert stale.outcome == StoreOutcome.CONFLICT_BLOCKED_EXPLICIT

    async with AsyncSessionLocal() as db:
        pace_versions = list((await db.execute(select(MemoryRecord).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
            MemoryRecord.memory_key == "travel.pace",
        ).order_by(MemoryRecord.version))).scalars().all())
    assert [(row.version, row.structured_value, row.is_active) for row in pace_versions] == [
        (1, "relaxed", False),
        (2, "moderately_packed", True),
    ]

    # 月 6：忘记安静酒店，旧来源重放仍由 tombstone 拦截。
    await _source_message("请忘记我喜欢安静酒店", 6)
    from backend.memory.service import MemoryService

    deleted = await MemoryService().delete_profile_memory(
        user_id=_USER, tenant_id=_TENANT, memory_id=quiet.memory_id,
    )
    assert deleted["ok"] is True
    replay = await _store(
        key="hotel.quiet", value="true", content="偏好安静酒店",
        source_id=month1, origin="inferred",
    )
    assert replay.outcome == StoreOutcome.BLOCKED_BY_DELETE

    # 月 7：新会话只召回仍有效的用户级偏好，不返回已删除或其他状态。
    async with AsyncSessionLocal() as db:
        await SessionRepository(db).get_or_create(_MONTH7_SESSION, _USER)
        await db.commit()
        profile = await MemoryService().get_profile(_USER, _TENANT)
        memory = LongTermMemory(MemoryRepository(db))
        memory._embedding_model = _Embedding()
        retrieved = await MemoryRepository(db).search_hybrid(
            memory.embedding.embed_query("旅行节奏偏中等紧凑"),
            _USER,
            tenant_id=_TENANT,
            domain="travel",
        )

    assert profile["profile"] == {
        "hotel": {"location": "near_metro"},
        "travel": {"pace": "moderately_packed"},
    }
    assert all("安静" not in item[0].content for item in retrieved)
    assert {item[0].memory_key for item in retrieved} <= {
        "hotel.location", "travel.pace",
    }
    assert any(item[0].memory_key == "travel.pace" for item in retrieved)

    async with AsyncSessionLocal() as db:
        temporary_text = (await db.execute(
            select(ChatMessage.content).where(
                ChatMessage.session_id == _MONTH1_SESSION,
                ChatMessage.id.in_((month1, month2, month3, month4, month5)),
            )
        )).scalars().all()
        active_keys = (await db.execute(select(MemoryRecord.memory_key).where(
            MemoryRecord.tenant_id == _TENANT,
            MemoryRecord.user_id == _USER,
            MemoryRecord.is_active.is_(True),
            MemoryRecord.memory_type != "tombstone",
        ))).scalars().all()
    assert len(temporary_text) == 5
    assert "hotel.quiet" not in active_keys
    assert "hotel.location" in active_keys and "travel.pace" in active_keys
