from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode_document(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class TripRepository:
    """只操作 travel_v2.trips；所有读取都要求租户和属主范围。"""

    def insert(
        self, cursor, *, trip_id: UUID, tenant_id: str, owner_id: str,
        title: str, document: dict, source_template_id: UUID | None = None,
        source_template_version: int | None = None,
    ) -> dict:
        cursor.execute(
            """INSERT INTO travel_v2.trips
                   (trip_id, tenant_id, owner_id, title, revision, document,
                    source_template_id, source_template_version)
               VALUES (%s, %s, %s, %s, 1, %s::jsonb, %s, %s)
               RETURNING trip_id, tenant_id, owner_id, title, status, revision,
                         document::text, source_template_id,
                         source_template_version, created_at, updated_at""",
            (str(trip_id), tenant_id, owner_id, title,
             json.dumps(document, ensure_ascii=False),
             str(source_template_id) if source_template_id else None,
             source_template_version),
        )
        return self._row(cursor.fetchone())

    def get(self, cursor, trip_id: UUID | str, *, tenant_id: str,
            owner_id: str, for_update: bool = False) -> dict | None:
        cursor.execute(
            """SELECT trip_id, tenant_id, owner_id, title, status, revision,
                      document::text, source_template_id,
                      source_template_version, created_at, updated_at
                 FROM travel_v2.trips
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND status = 'active'""" + (" FOR UPDATE" if for_update else ""),
            (str(trip_id), tenant_id, owner_id),
        )
        row = cursor.fetchone()
        return self._row(row) if row else None

    def list_active(self, cursor, *, tenant_id: str, owner_id: str,
                    limit: int = 50) -> list[dict]:
        cursor.execute(
            """SELECT trip_id, tenant_id, owner_id, title, status, revision,
                      document::text, source_template_id,
                      source_template_version, created_at, updated_at
                 FROM travel_v2.trips
                WHERE tenant_id = %s AND owner_id = %s AND status = 'active'
                ORDER BY updated_at DESC, trip_id
                LIMIT %s""",
            (tenant_id, owner_id, max(1, min(int(limit), 100))),
        )
        return [self._row(row) for row in cursor.fetchall()]

    def update_document_cas(
        self, cursor, *, trip_id: UUID | str, tenant_id: str, owner_id: str,
        expected_revision: int, title: str, document: dict,
    ) -> int:
        cursor.execute(
            """UPDATE travel_v2.trips
                  SET title = %s, document = %s::jsonb,
                      revision = revision + 1, updated_at = now()
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND status = 'active' AND revision = %s""",
            (title, json.dumps(document, ensure_ascii=False), str(trip_id),
             tenant_id, owner_id, expected_revision),
        )
        return cursor.rowcount

    def archive(self, cursor, trip_id: UUID | str, *, tenant_id: str,
                owner_id: str) -> bool:
        cursor.execute(
            """UPDATE travel_v2.trips SET status = 'archived', updated_at = now()
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND status = 'active'""",
            (str(trip_id), tenant_id, owner_id),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _row(row: tuple) -> dict:
        return {
            "trip_id": str(row[0]), "tenant_id": row[1], "owner_id": row[2],
            "title": row[3], "status": row[4], "revision": int(row[5]),
            "document": _decode_document(row[6]),
            "source_template_id": str(row[7]) if row[7] else None,
            "source_template_version": row[8],
            "created_at": row[9].isoformat(), "updated_at": row[10].isoformat(),
        }
