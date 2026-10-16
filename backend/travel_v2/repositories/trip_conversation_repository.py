from __future__ import annotations

from uuid import UUID


class TripConversationConflictError(ValueError):
    """会话已绑定到其他 Trip，或 Trip 的原始绑定不可更改。"""


class TripConversationRepository:
    """只读或首次写入 Trip 的原始规划会话关联。"""

    def get(self, cursor, trip_id: UUID | str, *, tenant_id: str,
            owner_id: str) -> str | None:
        cursor.execute(
            """SELECT conversation_id
                 FROM travel_v2.trip_conversations
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s""",
            (str(trip_id), tenant_id, owner_id),
        )
        row = cursor.fetchone()
        return str(row[0]) if row else None

    def insert(self, cursor, *, trip_id: UUID | str, tenant_id: str,
               owner_id: str, conversation_id: str) -> None:
        cursor.execute(
            """INSERT INTO travel_v2.trip_conversations
                   (trip_id, tenant_id, owner_id, conversation_id)
               VALUES (%s::uuid, %s, %s, %s)
               ON CONFLICT DO NOTHING
               RETURNING conversation_id""",
            (str(trip_id), tenant_id, owner_id, conversation_id),
        )
        if cursor.fetchone():
            return

        existing = self.get(
            cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
        if existing != conversation_id:
            raise TripConversationConflictError(
                "会话已绑定到其他行程，不能覆盖原始关联",
            )
