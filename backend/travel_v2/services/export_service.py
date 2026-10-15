from __future__ import annotations

from uuid import UUID

from backend.travel_v2.db import memory_connection
from backend.travel_v2.repositories.revision_repository import RevisionRepository
from backend.travel_v2.services.trip_service import TripNotFound


class RevisionExportService:
    """从正式 Revision 表读取指定快照，不接受客户端行程文档。"""

    def __init__(
        self,
        *,
        connect=memory_connection,
        revisions: RevisionRepository | None = None,
    ):
        self._connect = connect
        self._revisions = revisions or RevisionRepository()

    def get_snapshot(
        self,
        *,
        trip_id: str | UUID,
        tenant_id: str,
        owner_id: str,
        revision: int,
    ) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                result = self._revisions.get(
                    cursor,
                    trip_id,
                    revision,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                )
        if not result:
            raise TripNotFound("行程或指定 Revision 不存在")
        return result["snapshot"]
