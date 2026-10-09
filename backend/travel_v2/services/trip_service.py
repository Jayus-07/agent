from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.revision_repository import RevisionRepository
from backend.travel_v2.repositories.trip_repository import TripRepository

_TRIP_NAMESPACE = UUID("f5b971c4-aeb2-4ab2-a98c-7907e0a1ad81")


class TripNotFound(LookupError):
    """行程不存在、已归档或不属于当前租户/用户。"""


class IdempotencyConflict(ValueError):
    """幂等键被不同请求内容重复使用。"""


def _document_dict(document: TripDocumentV2 | dict) -> dict:
    parsed = document if isinstance(document, TripDocumentV2) else TripDocumentV2.model_validate(document)
    return parsed.model_dump(mode="json")


def _trip_id_for_key(tenant_id: str, owner_id: str, key: str) -> UUID:
    return uuid5(_TRIP_NAMESPACE, f"{tenant_id}\0{owner_id}\0{key}")


class TripService:
    def __init__(self, *, connect=memory_connection,
                 trip_repository: TripRepository | None = None,
                 revision_repository: RevisionRepository | None = None):
        self._connect = connect
        self._trips = trip_repository or TripRepository()
        self._revisions = revision_repository or RevisionRepository()

    def create_trip(
        self, *, tenant_id: str, owner_id: str, title: str,
        document: TripDocumentV2 | dict, actor_id: str,
        idempotency_key: str, source_template_id: str | UUID | None = None,
        source_template_version: int | None = None,
    ) -> dict:
        self._validate_scope(tenant_id, owner_id, actor_id)
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        title = str(title or "").strip()
        if not title or len(title) > 200:
            raise ValueError("title must contain 1 to 200 characters")
        snapshot = _document_dict(document)
        trip_id = _trip_id_for_key(tenant_id, owner_id, key)
        template_uuid = UUID(str(source_template_id)) if source_template_id else None

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (str(trip_id),),
                )
                existing = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )
                if existing:
                    same_request = (
                        existing["title"] == title
                        and existing["document"] == snapshot
                        and existing["source_template_id"] == (
                            str(template_uuid) if template_uuid else None
                        )
                        and existing["source_template_version"] == source_template_version
                    )
                    if not same_request:
                        raise IdempotencyConflict("Idempotency-Key 已用于不同的创建请求")
                    return {**existing, "replayed": True}
                trip = self._trips.insert(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, title=title, document=snapshot,
                    source_template_id=template_uuid,
                    source_template_version=source_template_version,
                )
                self._revisions.insert(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, revision=1, parent_revision=None,
                    snapshot=snapshot, change_type=(
                        "template_copy" if template_uuid else "create"
                    ),
                    change_summary=("复制平台模板" if template_uuid else "创建行程"),
                    actor_id=actor_id,
                )
        return {**trip, "replayed": False}

    def get_trip(self, trip_id: str | UUID, *, tenant_id: str,
                 owner_id: str) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                trip = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )
        if not trip:
            raise TripNotFound("行程不存在")
        return trip

    def list_trips(self, *, tenant_id: str, owner_id: str,
                   limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                return self._trips.list_active(
                    cursor, tenant_id=tenant_id, owner_id=owner_id, limit=limit,
                )

    def list_revisions(self, trip_id: str | UUID, *, tenant_id: str,
                       owner_id: str) -> list[dict]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if not self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                ):
                    raise TripNotFound("行程不存在")
                return self._revisions.list_for_trip(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )

    def archive_trip(self, trip_id: str | UUID, *, tenant_id: str,
                     owner_id: str) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                return self._trips.archive(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )

    @staticmethod
    def _validate_scope(tenant_id: str, owner_id: str, actor_id: str) -> None:
        if not tenant_id or not owner_id or not actor_id:
            raise ValueError("tenant_id, owner_id and actor_id are required")
        if len(tenant_id) > 128 or len(owner_id) > 128 or len(actor_id) > 128:
            raise ValueError("trip identity fields exceed 128 characters")


def canonical_request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
