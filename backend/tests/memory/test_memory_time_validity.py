"""记忆事实时间有效性回归（ADR-0012 第 2 期 D3）。

锁定语义：
- supersede = is_active=False + invalid_at 同一 UPDATE 原子双写（失效行不
  召回，invalid_at 供审计还原何时被取代）；
- 新行 valid_from 非空（事实生效时刻）、invalid_at NULL（未失效）；
- 版本链 superseded_by 回填不受影响（可审计还原的载体）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.tests.memory.conftest import require_memory_pg


def _require_time_columns() -> None:
    require_memory_pg()
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memory_records' "
                "AND column_name IN ('valid_from','invalid_at')"
            )
            found = {row[0] for row in cursor.fetchall()}
            if found != {"valid_from", "invalid_at"}:
                pytest.skip("145 迁移未应用（时间有效性列缺失），跳过")


@pytest.fixture()
def _isolated_user():
    require_memory_pg()
    _require_time_columns()
    user = f"tv-isol-{uuid.uuid4().hex[:10]}"
    tenant = "tenant-tv-isol"
    yield user, tenant
    import psycopg2

    from backend.config.database import MEMORY_DB_CONFIG

    with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM memory_records WHERE user_id LIKE %s",
                           (f"{user}%",))
        conn.commit()


class TestSupersedeTimeValidity:
    def test_supersede_marks_invalid_at_and_new_row_valid_from(
            self, _isolated_user):
        """同 key 新值 supersede：旧行失效标记 + 新行生效标记，版本链保留。"""
        from backend.memory.database import AsyncSessionLocal
        from backend.memory.keying import StoreOutcome
        from backend.memory.long_term import LongTermMemory, MemoryFact
        from backend.memory.repository.memory_repo import MemoryRepository

        user, tenant = _isolated_user
        session = f"{user}-sess"
        product = "customer_service"  # 第 2 期测试带产品维度（与第 1 期对照）

        async def _scenario():
            async with AsyncSessionLocal() as db:
                repo = MemoryRepository(db)
                l3 = LongTermMemory(repo)
                import hashlib as _hl

                class _FakeEmbedding:
                    def embed_query(self, text: str) -> list[float]:
                        seed = int(_hl.sha256(text.encode()).hexdigest()[:8], 16)
                        return [(seed >> i) & 1 for i in range(1024)]

                l3._embedding_model = _FakeEmbedding()

                fact_old = MemoryFact(
                    fact_type="preference", content=f"{user} 报告偏好简洁",
                    session_id=session, origin="explicit", confidence_score=0.9,
                    memory_key="preference.report_style", structured_value="concise")
                r1 = await l3.store_with_resolution(
                    fact_old, user, session, tenant, product=product)
                assert r1.outcome == StoreOutcome.INSERTED, r1
                old_id = r1.memory_id

                fact_new = MemoryFact(
                    fact_type="preference", content=f"{user} 报告偏好详细",
                    session_id=session, origin="explicit", confidence_score=0.95,
                    memory_key="preference.report_style", structured_value="detailed")
                r2 = await l3.store_with_resolution(
                    fact_new, user, session, tenant, product=product)
                assert r2.outcome == StoreOutcome.SUPERSEDED, r2

                # 同事务原子性验证：supersede 三步全部落库后的终态
                old_row = await repo.find_active_by_key(
                    tenant, user, "preference.report_style", product=product)
                await db.commit()

            # 旧行：is_active=False + invalid_at 非空 + superseded_by 指向新行
            import psycopg2

            from backend.config.database import MEMORY_DB_CONFIG

            with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT is_active, invalid_at, valid_from, superseded_by "
                        "FROM memory_records WHERE id = %s", (old_id,))
                    row = cursor.fetchone()
            assert row is not None, "superseded 旧行丢失"
            is_active, invalid_at, valid_from, superseded_by = row
            assert is_active is False
            assert invalid_at is not None, "旧行失效时刻未标记（D3 扩展缺失）"
            assert valid_from is not None
            assert superseded_by is not None

            # 新行：is_active=True + valid_from 非空 + invalid_at NULL
            with psycopg2.connect(**MEMORY_DB_CONFIG) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT is_active, invalid_at, valid_from, version "
                        "FROM memory_records WHERE id = %s", (r2.memory_id,))
                    nrow = cursor.fetchone()
            assert nrow is not None
            n_active, n_invalid_at, n_valid_from, n_version = nrow
            assert n_active is True
            assert n_invalid_at is None, "新行不得预标记失效"
            assert n_valid_from is not None
            assert n_version == 2, "版本单调递增"

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_scenario())
        finally:
            loop.close()
