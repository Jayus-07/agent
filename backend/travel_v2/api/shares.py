from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response

from backend.app.api.identity import require_identity
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.models.share import (
    CreatedTripShare,
    CreateTripShareRequest,
    PublicTripShare,
)
from backend.travel_v2.services.share_service import (
    ShareNotFoundError,
    ShareService,
    ShareTokenRecoveryError,
    project_public_share,
)
from backend.travel_v2.services.trip_service import IdempotencyConflict

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


def _persistence_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
            "message": "旅游 V2 分享暂不可用，请稍后重试",
        },
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
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128,
    ),
):
    tenant_id, owner_id = _owner_scope(request)
    try:
        created = ShareService().create_share(
            trip_id=trip_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            revision=payload.revision,
            idempotency_key=idempotency_key,
            expires_in_days=payload.expires_in_days,
        )
    except ShareNotFoundError as exc:
        raise _not_found(exc) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "Idempotency-Key 已用于不同的分享请求",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_SHARE_REQUEST",
                "message": "分享请求或 Idempotency-Key 格式无效",
            },
        ) from exc
    except TravelV2PersistenceError as exc:
        raise _persistence_unavailable() from exc
    except ShareTokenRecoveryError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "TRAVEL_V2_SHARE_UNAVAILABLE",
                "message": "分享暂不可用，请联系管理员检查服务配置",
            },
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # 令牌放在前端 URL fragment；浏览器不会把 fragment 发给服务器或放入 Referer。
    replayed = created.pop("replayed", False)
    if replayed:
        response.status_code = 200
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
    except TravelV2PersistenceError as exc:
        raise _persistence_unavailable() from exc
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
    except TravelV2PersistenceError as exc:
        raise _persistence_unavailable() from exc
    return Response(status_code=204)
