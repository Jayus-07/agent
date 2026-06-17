"""MemoryRepository — async CRUD + pgvector hybrid search for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import select, update, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from memory.models.memory import MemoryRecord


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def insert(self, record: MemoryRecord) -> MemoryRecord:
        if record.id is None:
            record.id = uuid4()
        self._s.add(record)
        await self._s.flush()
        return record

    async def search_hybrid(
        self, embedding: list[float], user_id: str, top_k: int = 20,
        memory_type: str | None = None,
    ) -> list[MemoryRecord]:
        """pgvector cosine similarity + importance + recency joint scoring"""
        query = select(
            MemoryRecord,
            (1.0 - (MemoryRecord.embedding.cosine_distance(embedding))).label("similarity"),
        ).where(
            MemoryRecord.is_active == True,
            MemoryRecord.user_id == user_id,
        )
        if memory_type:
            query = query.where(MemoryRecord.memory_type == memory_type)
        query = query.order_by(text("similarity DESC")).limit(top_k)

        result = await self._s.execute(query)
        return [row[0] for row in result.all()]

    async def find_similar(
        self, embedding: list[float], user_id: str, threshold: float = 0.85,
    ) -> MemoryRecord | None:
        """Find most similar active record above threshold"""
        result = await self._s.execute(
            select(MemoryRecord, (1.0 - MemoryRecord.embedding.cosine_distance(embedding)).label("sim"))
            .where(MemoryRecord.is_active == True, MemoryRecord.user_id == user_id)
            .having(text(f"1.0 - (embedding <=> :emb) >= {threshold}"))
            .order_by(text("sim DESC")).limit(1),
            {"emb": embedding},
        )
        row = result.first()
        return row[0] if row else None

    async def supersede(self, old_id: str, new_id: str) -> bool:
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == old_id, MemoryRecord.is_active == True)
            .values(is_active=False, superseded_by=new_id)
        )
        return result.rowcount > 0

    async def mark_accessed(self, ids: list[str]) -> None:
        await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id.in_(ids))
            .values(access_count=MemoryRecord.access_count + 1, last_access_at=datetime.now(timezone.utc))
        )

    async def find_by_id(self, record_id: str) -> MemoryRecord | None:
        result = await self._s.execute(select(MemoryRecord).where(MemoryRecord.id == record_id))
        return result.scalar_one_or_none()

    async def update_fields(self, record_id: str, **fields) -> bool:
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == record_id).values(**fields)
        )
        return result.rowcount > 0

    async def apply_decay(self, days: int, factor: float) -> int:
        threshold = datetime.now(timezone.utc).isoformat()
        result = await self._s.execute(
            text("""
                UPDATE memory_records
                SET importance_score = importance_score * :factor
                WHERE is_active = TRUE
                  AND last_access_at < NOW() - (:days || ' days')::INTERVAL
            """),
            {"factor": factor, "days": str(days)},
        )
        return result.rowcount

    async def archive_stale(self, min_importance: float = 0.2) -> int:
        result = await self._s.execute(
            update(MemoryRecord)
            .where(MemoryRecord.is_active == True, MemoryRecord.importance_score < min_importance)
            .values(is_active=False)
        )
        return result.rowcount

    async def count_active(self) -> int:
        result = await self._s.execute(select(func.count()).where(MemoryRecord.is_active == True))
        return result.scalar() or 0
