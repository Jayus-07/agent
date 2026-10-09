from __future__ import annotations

from uuid import UUID

from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.travel_v2.services.trip_service import TripService


class RevisionService:
    """正式 Revision 历史查询与恢复入口。"""

    def __init__(self, *, trips: TripService | None = None,
                 edits: TripEditService | None = None):
        self._trips = trips or TripService()
        self._edits = edits or TripEditService()

    def list_revisions(self, trip_id: str | UUID, *, tenant_id: str,
                       owner_id: str) -> list[dict]:
        return self._trips.list_revisions(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )

    def restore(self, *, trip_id: str | UUID, tenant_id: str,
                owner_id: str, actor_id: str, expected_revision: int,
                target_revision: int, idempotency_key: str) -> dict:
        return self._edits.restore(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=expected_revision,
            target_revision=target_revision,
            idempotency_key=idempotency_key,
        )
