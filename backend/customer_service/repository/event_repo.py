"""event_repo.py — cs_events 事件表读写（P3.2）

- append：事件落库（拿全局自增 id 作 seq），重复 event_id 幂等返回已有 id
- replay：断线补发，WHERE conversation_id AND id > after_seq ORDER BY id

事件落库是尽力而为：调用方（realtime.publish）失败只记日志不影响广播。
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.customer_service.models.event import CSEvent


class EventRepository:
    def __init__(self, db):
        self._db = db

    async def append(
        self,
        *,
        conversation_id: str,
        event_id: str,
        type: str,
        payload: dict,
    ) -> int:
        """落库并返回 seq（即主键 id）。event_id 冲突 → 返回已存在行的 id。"""
        stmt = (
            pg_insert(CSEvent)
            .values(
                conversation_id=conversation_id,
                event_id=event_id,
                type=type,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(CSEvent.id)
        )
        row = (await self._db.execute(stmt)).first()
        if row is not None:
            await self._db.commit()
            return int(row[0])
        # 冲突：同 event_id 已落过库（publish 重试/多实例并发）→ 回读原 id
        from sqlalchemy import select

        existing = (
            await self._db.execute(
                select(CSEvent.id).where(CSEvent.event_id == event_id)
            )
        ).scalar_one()
        return int(existing)

    async def replay(
        self,
        conversation_id: str,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        """按 seq 升序回放 > after_seq 的事件（补发用）。"""
        from sqlalchemy import select

        rows = (
            (
                await self._db.execute(
                    select(CSEvent)
                    .where(
                        CSEvent.conversation_id == conversation_id,
                        CSEvent.id > after_seq,
                    )
                    .order_by(CSEvent.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "seq": r.id,
                "event_id": r.event_id,
                "type": r.type,
                "payload": r.payload,
                "ts": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
