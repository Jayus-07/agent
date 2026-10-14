from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Request, Response

from backend.app.api.identity import require_identity
from backend.app.api.routes.travel import ics_content_disposition
from backend.travel_v2.adapters.ics_export import trip_document_to_ics
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.services.export_service import RevisionExportService
from backend.travel_v2.services.share_service import ShareNotFoundError, ShareService
from backend.travel_v2.services.trip_service import TripNotFound

router = APIRouter(tags=["旅游 V2 Revision 导出"])


def _owner_scope(request: Request) -> tuple[str, str]:
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TRAVEL_V2_IDENTITY_INCOMPLETE",
                "message": "旅游 V2 导出需要完整的用户与租户身份",
            },
        )
    return tenant_id, owner_id


def _ics_response(
    snapshot: dict, *, filename: str, share_token_access: bool = False,
) -> Response:
    try:
        content = trip_document_to_ics(snapshot)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRAVEL_V2_ICS_INVALID_SCHEDULE",
                "message": str(exc),
            },
        ) from exc
    headers = {
        "Content-Disposition": ics_content_disposition(filename),
        "Cache-Control": "no-store, private",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
    }
    if share_token_access:
        headers["Vary"] = "X-Travel-Share-Token"
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers=headers,
    )


@router.get(
    "/travel/v2/trips/{trip_id}/revisions/{revision}/export.ics",
    summary="导出指定 Trip Revision 的 ICS 日历",
    name="export_travel_v2_revision_ics",
)
def export_trip_revision_ics(
    trip_id: UUID,
    request: Request,
    revision: int = Path(ge=1),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        snapshot = RevisionExportService().get_snapshot(
            trip_id=trip_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            revision=revision,
        )
    except TripNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": str(exc),
            },
        ) from exc
    except TravelV2PersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
                "message": "旅游 V2 导出暂不可用，请稍后重试",
            },
        ) from exc
    document = snapshot
    try:
        filename = document["brief"]["destination"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRAVEL_V2_ICS_INVALID_SCHEDULE",
                "message": "Revision 缺少导出所需的目的地",
            },
        ) from exc
    return _ics_response(snapshot, filename=filename)


@router.get(
    "/travel/v2/shares/{share_id}/export.ics",
    summary="使用分享令牌导出固定 Revision 的 ICS 日历",
    name="export_travel_v2_share_ics",
)
def export_shared_revision_ics(
    share_id: UUID,
    share_token: str = Header(default="", alias="X-Travel-Share-Token"),
):
    try:
        share = ShareService().get_public_share(share_id=share_id, token=share_token)
    except ShareNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": str(exc),
            },
        ) from exc
    except TravelV2PersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
                "message": "旅游 V2 导出暂不可用，请稍后重试",
            },
        ) from exc
    try:
        filename = share["snapshot"]["brief"]["destination"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TRAVEL_V2_ICS_INVALID_SCHEDULE",
                "message": "Revision 缺少导出所需的目的地",
            },
        ) from exc
    return _ics_response(
        share["snapshot"], filename=filename, share_token_access=True,
    )
