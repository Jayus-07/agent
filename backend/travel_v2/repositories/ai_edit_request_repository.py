from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


class AIEditRequestRepository:
    """在模型调用前占位，记录 AI 原始请求的幂等状态。"""

    def get(self, cursor, *, trip_id: UUID | str, tenant_id: str,
            owner_id: str, idempotency_key: str,
            for_update: bool = False) -> dict | None:
        cursor.execute(
            """SELECT request_id, request_sha256, expected_revision, status,
                      response::text, metadata::text, created_at, updated_at
                 FROM travel_v2.ai_edit_requests
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND idempotency_key = %s""" + (" FOR UPDATE" if for_update else ""),
            (str(trip_id), tenant_id, owner_id, idempotency_key),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "request_id": str(row[0]), "request_sha256": row[1].strip(),
            "expected_revision": int(row[2]), "status": row[3],
            "response": _decode(row[4]), "metadata": _decode(row[5]),
            "created_at": row[6], "updated_at": row[7],
        }

    def get_pending(self, cursor, *, trip_id: UUID | str, tenant_id: str,
                    owner_id: str, pending_id: UUID | str,
                    for_update: bool = False) -> dict | None:
        cursor.execute(
            """SELECT request_id, idempotency_key, request_sha256,
                      expected_revision, status, response::text, metadata::text,
                      created_at, updated_at
                 FROM travel_v2.ai_edit_requests
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND response->>'pending_id' = %s
                  AND metadata ? 'pending_resolution'"""
            + (" FOR UPDATE" if for_update else ""),
            (str(trip_id), tenant_id, owner_id, str(pending_id)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "request_id": str(row[0]), "idempotency_key": row[1],
            "request_sha256": row[2].strip(),
            "expected_revision": int(row[3]), "status": row[4],
            "response": _decode(row[5]), "metadata": _decode(row[6]),
            "created_at": row[7], "updated_at": row[8],
        }

    def reserve(self, cursor, *, request_id: UUID, trip_id: UUID | str,
                tenant_id: str, owner_id: str, idempotency_key: str,
                request_sha256: str, expected_revision: int) -> bool:
        cursor.execute(
            """INSERT INTO travel_v2.ai_edit_requests
                   (request_id, trip_id, tenant_id, owner_id, idempotency_key,
                    request_sha256, expected_revision, status)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, 'processing')
               ON CONFLICT (trip_id, idempotency_key) DO NOTHING
               RETURNING request_id""",
            (str(request_id), str(trip_id), tenant_id, owner_id,
             idempotency_key, request_sha256, expected_revision),
        )
        return cursor.fetchone() is not None

    def finish(self, cursor, *, trip_id: UUID | str, tenant_id: str,
               owner_id: str, idempotency_key: str, status: str,
               response: dict, metadata: dict | None = None,
               allow_unknown: bool = False) -> None:
        cursor.execute(
            """UPDATE travel_v2.ai_edit_requests
                   SET status = %s, response = %s::jsonb,
                       metadata = %s::jsonb, updated_at = now()
                 WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                   AND idempotency_key = %s
                   AND (status = 'processing' OR (%s AND status = 'unknown'))""",
            (status, json.dumps(response, ensure_ascii=False),
             json.dumps(metadata or {}, ensure_ascii=False), str(trip_id),
             tenant_id, owner_id, idempotency_key, allow_unknown),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("AI 编辑请求状态无法完成")

    def transition_pending(
        self, cursor, *, trip_id: UUID | str, tenant_id: str,
        owner_id: str, idempotency_key: str, status: str,
        response: dict,
    ) -> None:
        """只允许已挂起请求做一次终态转换。"""
        cursor.execute(
            """UPDATE travel_v2.ai_edit_requests
                  SET status = %s, response = %s::jsonb, updated_at = now()
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND idempotency_key = %s AND status = 'pending_resolution'""",
            (status, json.dumps(response, ensure_ascii=False), str(trip_id),
             tenant_id, owner_id, idempotency_key),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("待解决请求状态已变化")

    def mark_stale_unknown(self, cursor, *, trip_id: UUID | str,
                           tenant_id: str, owner_id: str,
                           idempotency_key: str) -> None:
        cursor.execute(
            """UPDATE travel_v2.ai_edit_requests
                  SET status = 'unknown',
                      response = jsonb_build_object(
                          'status', 'unknown', 'saved', false,
                          'trip_id', trip_id::text,
                          'base_revision', expected_revision,
                          'operation_key', idempotency_key,
                          'code', 'AI_EDIT_OUTCOME_UNKNOWN'),
                      updated_at = now()
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND idempotency_key = %s AND status = 'processing'
                  AND updated_at < now() - interval '90 seconds'""",
            (str(trip_id), tenant_id, owner_id, idempotency_key),
        )
