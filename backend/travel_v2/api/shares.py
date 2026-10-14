from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response

from backend.app.api.identity import require_identity
from backend.travel_v2.models.share import (
    CreatedTripShare,
    CreateTripShareRequest,
    PublicTripShare,
)
from backend.travel_v2.services.share_service import (
    ShareNotFoundError,
    ShareService,
    project_public_share,
)

router = APIRouter(tags=["旅游 V2 固定版本分享"])


def _owner_scope(request: Request) -> tuple[str, str]:
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TRAVEL_V2_IDENTITY_INCOMPLETE",
                "message": "旅游 V2 分享需要完整的用户与租户身份",
            },
        )
    return tenant_id, owner_id


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": str(exc)},
    )


@router.post(
    "/travel/v2/trips/{trip_id}/shares",
    status_code=201,
    response_model=CreatedTripShare,
    summary="为指定 Trip Revision 创建只读分享",
    name="create_travel_v2_share",
)
def create_trip_share(
    trip_id: UUID,
    payload: CreateTripShareRequest,
    request: Request,
    response: Response,
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        created = ShareService().create_share(
            trip_id=trip_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            revision=payload.revision,
            expires_in_days=payload.expires_in_days,
        )
    except ShareNotFoundError as exc:
        raise _not_found(exc) from exc

    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # 令牌放在前端 URL fragment；浏览器不会把 fragment 发给服务器或放入 Referer。
    return {
        "share_id": created["share_id"],
        "trip_id": created["trip_id"],
        "revision": created["revision"],
        "expires_at": created["expires_at"],
        "created_at": created["created_at"],
        "share_url": f"/travel/share/{created['share_id']}#token={created['token']}",
    }


@router.get(
    "/travel/v2/shares/{share_id}",
    summary="使用分享令牌读取固定 Revision 的公开字段",
    response_model=PublicTripShare,
    name="get_travel_v2_share",
)
def get_trip_share(
    share_id: UUID,
    response: Response,
    share_token: str = Header(default="", alias="X-Travel-Share-Token"),
):
    try:
        share = ShareService().get_public_share(share_id=share_id, token=share_token)
    except ShareNotFoundError as exc:
        raise _not_found(exc) from exc
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Vary"] = "X-Travel-Share-Token"
    return project_public_share(share)


@router.delete(
    "/travel/v2/trips/{trip_id}/shares/{share_id}",
    status_code=204,
    summary="撤销当前用户 Trip 的分享",
    name="revoke_travel_v2_share",
)
def revoke_trip_share(trip_id: UUID, share_id: UUID, request: Request):
    tenant_id, owner_id = _owner_scope(request)
    try:
        ShareService().revoke_share(
            share_id=share_id,
            trip_id=trip_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except ShareNotFoundError as exc:
        raise _not_found(exc) from exc
    return Response(status_code=204)
