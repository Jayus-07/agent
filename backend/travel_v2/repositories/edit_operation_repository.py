from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class EditOperationRepository:
    """只记录成功操作，由服务在所有写入成功前后同事务调用。"""

    def get_by_key(self, cursor, trip_id: UUID | str, idempotency_key: str,
                   *, tenant_id: str, owner_id: str) -> dict | None:
        cursor.execute(
            """SELECT operation_id, trip_id, tenant_id, owner_id,
                      idempotency_key, request_sha256, command_type,
                      base_revision, result_revision, response::text, created_at
                 FROM travel_v2.edit_operations
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND idempotency_key = %s""",
            (str(trip_id), tenant_id, owner_id, idempotency_key),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "operation_id": str(row[0]), "trip_id": str(row[1]),
            "tenant_id": row[2], "owner_id": row[3],
            "idempotency_key": row[4], "request_sha256": row[5].strip(),
            "command_type": row[6], "base_revision": int(row[7]),
            "result_revision": int(row[8]), "response": _decode(row[9]),
            "created_at": row[10].isoformat(),
        }

    def insert(
        self, cursor, *, operation_id: UUID, trip_id: UUID | str,
        tenant_id: str, owner_id: str, idempotency_key: str,
        request_sha256: str, command_type: str, base_revision: int,
        result_revision: int, response: dict,
    ) -> None:
        cursor.execute(
            """INSERT INTO travel_v2.edit_operations
                   (operation_id, trip_id, tenant_id, owner_id, idempotency_key,
                    request_sha256, command_type, base_revision, result_revision,
                    response)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (str(operation_id), str(trip_id), tenant_id, owner_id,
             idempotency_key, request_sha256, command_type, base_revision,
             result_revision, json.dumps(response, ensure_ascii=False)),
        )
