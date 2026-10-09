from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class RevisionRepository:
    """完整快照只插入，不提供 UPDATE。"""

    def insert(
        self, cursor, *, trip_id: UUID | str, tenant_id: str, owner_id: str,
        revision: int, parent_revision: int | None, snapshot: dict,
        change_type: str, change_summary: str, actor_id: str,
    ) -> dict:
        cursor.execute(
            """INSERT INTO travel_v2.trip_revisions
                   (trip_id, tenant_id, owner_id, revision, parent_revision,
                    snapshot, change_type, change_summary, actor_id)
               VALUES (%s::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
               RETURNING trip_id, tenant_id, owner_id, revision, parent_revision,
                         snapshot::text, change_type, change_summary, actor_id,
                         created_at""",
            (str(trip_id), tenant_id, owner_id, revision, parent_revision,
             json.dumps(snapshot, ensure_ascii=False), change_type,
             change_summary, actor_id),
        )
        return self._row(cursor.fetchone())

    def get(self, cursor, trip_id: UUID | str, revision: int, *,
            tenant_id: str, owner_id: str) -> dict | None:
        cursor.execute(
            """SELECT r.trip_id, r.tenant_id, r.owner_id, r.revision,
                      r.parent_revision, r.snapshot::text, r.change_type,
                      r.change_summary, r.actor_id, r.created_at
                 FROM travel_v2.trip_revisions r
                 JOIN travel_v2.trips t
                   ON t.trip_id = r.trip_id AND t.tenant_id = r.tenant_id
                  AND t.owner_id = r.owner_id
                WHERE r.trip_id = %s::uuid AND r.tenant_id = %s AND r.owner_id = %s
                  AND r.revision = %s AND t.status = 'active'""",
            (str(trip_id), tenant_id, owner_id, revision),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def list_for_trip(self, cursor, trip_id: UUID | str, *,
                      tenant_id: str, owner_id: str) -> list[dict]:
        cursor.execute(
            """SELECT r.trip_id, r.tenant_id, r.owner_id, r.revision,
                      r.parent_revision, r.snapshot::text, r.change_type,
                      r.change_summary, r.actor_id, r.created_at
                 FROM travel_v2.trip_revisions r
                 JOIN travel_v2.trips t
                   ON t.trip_id = r.trip_id AND t.tenant_id = r.tenant_id
                  AND t.owner_id = r.owner_id
                WHERE r.trip_id = %s::uuid AND r.tenant_id = %s AND r.owner_id = %s
                  AND t.status = 'active'
                ORDER BY r.revision DESC""",
            (str(trip_id), tenant_id, owner_id),
        )
        return [self._row(row) for row in cursor.fetchall()]

    @staticmethod
    def _row(row: tuple) -> dict:
        return {
            "trip_id": str(row[0]), "tenant_id": row[1], "owner_id": row[2],
            "revision": int(row[3]),
            "parent_revision": int(row[4]) if row[4] is not None else None,
            "snapshot": _decode(row[5]), "change_type": row[6],
            "change_summary": row[7], "actor_id": row[8],
            "created_at": row[9].isoformat(),
        }
