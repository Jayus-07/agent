from __future__ import annotations

import json
from typing import Any
from uuid import UUID


def _decode(raw: Any) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class ShareRepository:
    """固定 Revision 分享记录；令牌仅存 SHA-256，幂等身份仅存摘要。"""

    def get_revision_for_owner(
        self,
        cursor,
        *,
        trip_id: UUID | str,
        tenant_id: str,
        owner_id: str,
        revision: int,
        for_update: bool = False,
    ) -> dict | None:
        cursor.execute(
            """SELECT r.snapshot::text
                 FROM travel_v2.trips t
                 JOIN travel_v2.trip_revisions r
                   ON r.trip_id = t.trip_id
                WHERE t.trip_id = %s::uuid AND t.tenant_id = %s
                  AND t.owner_id = %s AND t.status = 'active'
                  AND r.revision = %s"""
            + (" FOR UPDATE OF t" if for_update else ""),
            (str(trip_id), tenant_id, owner_id, revision),
        )
        row = cursor.fetchone()
        return {"snapshot": _decode(row[0])} if row else None

    def insert(
        self,
        cursor,
        *,
        share_id: UUID,
        trip_id: UUID | str,
        tenant_id: str,
        owner_id: str,
        revision: int,
        token_sha256: str,
        idempotency_key_sha256: str,
        request_sha256: str,
        expires_in_days: int,
    ) -> dict:
        cursor.execute(
            """INSERT INTO travel_v2.trip_shares
                   (share_id, trip_id, tenant_id, owner_id, revision,
                    token_sha256, idempotency_key_sha256, request_sha256,
                    expires_at)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s,
                       now() + (%s * interval '1 day'))
               RETURNING share_id, trip_id, revision, expires_at, created_at""",
            (
                str(share_id),
                str(trip_id),
                tenant_id,
                owner_id,
                revision,
                token_sha256,
                idempotency_key_sha256,
                request_sha256,
                expires_in_days,
            ),
        )
        row = cursor.fetchone()
        return {
            "share_id": str(row[0]),
            "trip_id": str(row[1]),
            "revision": int(row[2]),
            "expires_at": row[3].isoformat(),
            "created_at": row[4].isoformat(),
        }

    def get_by_idempotency_key(
        self,
        cursor,
        *,
        trip_id: UUID | str,
        tenant_id: str,
        owner_id: str,
        idempotency_key_sha256: str,
    ) -> dict | None:
        cursor.execute(
            """SELECT share_id, trip_id, revision, expires_at, created_at,
                      token_sha256, request_sha256
                 FROM travel_v2.trip_shares
                WHERE trip_id = %s::uuid AND tenant_id = %s AND owner_id = %s
                  AND idempotency_key_sha256 = %s
                FOR UPDATE""",
            (str(trip_id), tenant_id, owner_id, idempotency_key_sha256),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "share": {
                "share_id": str(row[0]),
                "trip_id": str(row[1]),
                "revision": int(row[2]),
                "expires_at": row[3].isoformat(),
                "created_at": row[4].isoformat(),
            },
            "token_sha256": row[5],
            "request_sha256": row[6],
        }

    def get_public(
        self,
        cursor,
        *,
        share_id: UUID | str,
        token_sha256: str,
    ) -> dict | None:
        cursor.execute(
            """SELECT s.share_id, s.revision, s.expires_at, s.created_at,
                      r.snapshot::text
                 FROM travel_v2.trip_shares s
                 JOIN travel_v2.trips t
                   ON t.trip_id = s.trip_id AND t.tenant_id = s.tenant_id
                  AND t.owner_id = s.owner_id
                 JOIN travel_v2.trip_revisions r
                   ON r.trip_id = s.trip_id AND r.revision = s.revision
                WHERE s.share_id = %s::uuid AND s.token_sha256 = %s
                  AND s.revoked_at IS NULL AND s.expires_at > now()
                  AND t.status = 'active'""",
            (str(share_id), token_sha256),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "share_id": str(row[0]),
            "revision": int(row[1]),
            "expires_at": row[2].isoformat(),
            "created_at": row[3].isoformat(),
            "snapshot": _decode(row[4]),
        }

    def revoke_for_owner(
        self,
        cursor,
        *,
        share_id: UUID | str,
        trip_id: UUID | str,
        tenant_id: str,
        owner_id: str,
    ) -> bool:
        cursor.execute(
            """UPDATE travel_v2.trip_shares
                  SET revoked_at = COALESCE(revoked_at, now())
                WHERE share_id = %s::uuid AND trip_id = %s::uuid
                  AND tenant_id = %s AND owner_id = %s
               RETURNING share_id""",
            (str(share_id), str(trip_id), tenant_id, owner_id),
        )
        return cursor.fetchone() is not None
