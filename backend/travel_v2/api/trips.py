from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from backend.app.api.identity import require_identity
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.models.commands import (
    AddMealSelectionCommand,
    CreateTripRequest,
    ReplaceTripDocumentCommand,
    RestoreRevisionCommand,
    SelectIntercityTrainCommand,
    SelectLodgingCommand,
    StructuredTripEditCommand,
)
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    RevisionNotFound,
    TripEditService,
    VersionConflict,
)
from backend.travel_v2.services.revision_service import RevisionService
from backend.travel_v2.services.trip_service import TripNotFound, TripService

router = APIRouter(tags=["旅游 V2 行程"])


def _scope(request: Request) -> tuple[str, str, str]:
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(status_code=403, detail={
            "code": "TRAVEL_V2_IDENTITY_INCOMPLETE",
            "message": "旅游 V2 行程需要完整的用户与租户身份",
        })
    return tenant_id, owner_id, owner_id


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (TripNotFound, RevisionNotFound)):
        return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)})
    if isinstance(exc, (VersionConflict, IdempotencyConflict)):
        code = "VERSION_CONFLICT" if isinstance(exc, VersionConflict) else "IDEMPOTENCY_CONFLICT"
        return HTTPException(status_code=409, detail={"code": code, "message": str(exc)})
    if isinstance(exc, TravelV2PersistenceError):
        return HTTPException(status_code=503, detail={
            "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
            "message": "行程没有保存，请稍后重试",
        })
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail={"code": "INVALID_TRIP_COMMAND", "message": str(exc)})
    raise exc


@router.get("/travel/v2/trips", summary="列出当前用户的旅游 V2 行程")
def list_trips(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        return {"trips": TripService().list_trips(
            tenant_id=tenant_id, owner_id=owner_id, limit=limit,
        )}
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips", summary="创建旅游 V2 正式行程", status_code=201)
def create_trip(
    payload: CreateTripRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        result = TripService().create_trip(
            tenant_id=tenant_id, owner_id=owner_id,
            title=payload.title, document=payload.document,
            actor_id=actor_id, idempotency_key=idempotency_key,
        )
        if result.pop("replayed", False):
            response.status_code = 200
        return result
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/travel/v2/trips/{trip_id}", summary="读取旅游 V2 正式行程")
def get_trip(trip_id: UUID, request: Request):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        return TripService().get_trip(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/travel/v2/trips/{trip_id}", summary="归档当前用户的旅游 V2 行程")
def archive_trip(trip_id: UUID, request: Request):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        archived = TripService().archive_trip(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
        if not archived:
            raise TripNotFound("行程不存在")
        return {"trip_id": str(trip_id), "status": "archived", "saved": True}
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/travel/v2/trips/{trip_id}/revisions", summary="查询旅游 V2 行程快照历史")
def list_revisions(trip_id: UUID, request: Request):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        return {"trip_id": str(trip_id), "revisions": RevisionService().list_revisions(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )}
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/travel/v2/trips/{trip_id}/document", summary="以新 Revision 原子保存行程")
def edit_trip(
    trip_id: UUID,
    payload: ReplaceTripDocumentCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return TripEditService().apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key, command_type=payload.command_type,
            change_summary=payload.change_summary, document=payload.document,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips/{trip_id}/edits", summary="应用一项结构化行程编辑")
def apply_trip_edit(
    trip_id: UUID,
    payload: StructuredTripEditCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return TripEditService().apply_operation(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, idempotency_key=idempotency_key,
            command=payload,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips/{trip_id}/selections/meals", summary="将已选美食加入指定日期")
def add_meal_selection(
    trip_id: UUID,
    payload: AddMealSelectionCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return TripEditService().add_meal(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, idempotency_key=idempotency_key,
            command=payload,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips/{trip_id}/selections/lodgings", summary="将已选酒店保存为未预订住宿参考")
def select_lodging(
    trip_id: UUID,
    payload: SelectLodgingCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return TripEditService().select_lodging(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, idempotency_key=idempotency_key,
            command=payload,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips/{trip_id}/selections/intercity-trains", summary="将已选动车保存为未购票城际交通参考")
def select_intercity_train(
    trip_id: UUID,
    payload: SelectIntercityTrainCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return TripEditService().select_intercity_train(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, idempotency_key=idempotency_key,
            command=payload,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/trips/{trip_id}/restore", summary="恢复历史内容并产生新 Revision")
def restore_trip(
    trip_id: UUID,
    payload: RestoreRevisionCommand,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        return RevisionService().restore(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=payload.expected_revision,
            target_revision=payload.target_revision,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _error(exc) from exc
