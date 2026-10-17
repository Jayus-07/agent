from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from backend.app.api.identity import require_identity
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.models.ai_edit import (
    AITripEditRequest,
    AITripEditResolutionRequest,
)
from backend.travel_v2.models.commands import (
    AddMealSelectionCommand,
    CreateTripRequest,
    ReplaceTripDocumentCommand,
    RestoreRevisionCommand,
    SelectIntercityTrainCommand,
    SelectLodgingCommand,
    StructuredTripEditCommand,
)
from backend.travel_v2.services.revision_service import RevisionService
from backend.travel_v2.services.trip_ai_edit_service import TripAIEditService
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    RevisionNotFound,
    TripEditService,
    VersionConflict,
)
from backend.travel_v2.services.trip_service import (
    IdempotencyConflict as TripCreateIdempotencyConflict,
)
from backend.travel_v2.services.trip_service import (
    TripNotFound,
    TripService,
)
from backend.travel_v2.services.trip_write_validator import TripWriteConflictError

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
    if isinstance(exc, (VersionConflict, IdempotencyConflict, TripCreateIdempotencyConflict)):
        code = "VERSION_CONFLICT" if isinstance(exc, VersionConflict) else "IDEMPOTENCY_CONFLICT"
        return HTTPException(status_code=409, detail={"code": code, "message": str(exc)})
    if isinstance(exc, TravelV2PersistenceError):
        return HTTPException(status_code=503, detail={
            "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
            "message": "行程没有保存，请稍后重试",
        })
    if isinstance(exc, TripWriteConflictError):
        return HTTPException(status_code=422, detail={
            "code": "TRIP_WRITE_CONFLICT",
            "conflicts": [exc.conflict],
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
            conversation_id=payload.conversation_id,
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


@router.get(
    "/travel/v2/trips/{trip_id}/conversation",
    summary="按 Trip 属主权限恢复其原始规划会话",
)
def get_trip_conversation(trip_id: UUID, request: Request):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        return TripService().get_trip_conversation(
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


@router.post("/travel/v2/trips/{trip_id}/ai-edits", summary="通过自然语言生成并校验一项 Trip 修改")
def apply_trip_ai_edit(
    trip_id: UUID,
    payload: AITripEditRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        result = TripAIEditService().edit(
            trip_id=str(trip_id), tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, idempotency_key=idempotency_key,
            request=payload,
        )
    except Exception as exc:
        raise _error(exc) from exc

    status = result.get("status")
    http_status = 200
    if status in {"processing", "unknown"}:
        http_status = 202
    elif status == "pending_resolution":
        http_status = 200
    elif status == "failed":
        code = result.get("code")
        if code == "VERSION_CONFLICT":
            http_status = 409
        elif code == "IDEMPOTENCY_CONFLICT":
            http_status = 409
        elif code == "AI_EDIT_INVALID_OUTPUT":
            http_status = 502
        elif code == "AI_EDIT_OUTCOME_UNKNOWN":
            http_status = 202
        elif code == "AI_EDIT_CALL_UNKNOWN":
            http_status = 202
        elif code in {
            "AI_EDIT_CANDIDATE_REJECTED", "AI_EDIT_VALIDATION_REJECTED",
            "AI_EDIT_CONFLICT_BLOCKED",
        }:
            http_status = 422
        else:
            http_status = 503
    return JSONResponse(status_code=http_status, content=result)


@router.get(
    "/travel/v2/trips/{trip_id}/ai-edits/{operation_key}",
    summary="只读查询旅游 V2 AI 编辑操作终态",
)
def get_trip_ai_edit_status(
    trip_id: UUID, operation_key: str, request: Request,
):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        result = TripAIEditService().operation_status(
            trip_id=str(trip_id), tenant_id=tenant_id, owner_id=owner_id,
            operation_key=operation_key,
        )
    except Exception as exc:
        raise _error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={
            "code": "AI_EDIT_OPERATION_NOT_FOUND",
            "message": "当前用户范围内没有该编辑操作。",
        })
    return result


@router.get(
    "/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}",
    summary="查询旅游 V2 AI 编辑待解决冲突",
)
def get_trip_ai_pending_resolution(
    trip_id: UUID, pending_id: UUID, request: Request,
):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        result = TripAIEditService().get_pending_resolution(
            trip_id=str(trip_id), tenant_id=tenant_id, owner_id=owner_id,
            pending_id=str(pending_id),
        )
    except Exception as exc:
        raise _error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={
            "code": "AI_EDIT_PENDING_NOT_FOUND",
            "message": "当前用户范围内没有该待解决操作。",
        })
    return result


@router.post(
    "/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/resolve",
    summary="确认一项服务端提供的 AI 冲突解决选项",
)
def resolve_trip_ai_pending(
    trip_id: UUID, pending_id: UUID, payload: AITripEditResolutionRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    tenant_id, owner_id, actor_id = _scope(request)
    try:
        result = TripAIEditService().resolve_pending_resolution(
            trip_id=str(trip_id), tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, pending_id=str(pending_id),
            resolution_id=payload.resolution_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _error(exc) from exc
    code = result.get("code")
    if result.get("status") == "processing":
        status_code = 202
    elif result.get("status") == "failed":
        status_code = 410 if code == "PENDING_EXPIRED" else 409
        if code in {"INVALID_RESOLUTION", "AI_EDIT_VALIDATION_REJECTED"}:
            status_code = 422
    else:
        status_code = 200
    return JSONResponse(status_code=status_code, content=result)


@router.post(
    "/travel/v2/trips/{trip_id}/ai-edits/pending/{pending_id}/cancel",
    summary="取消或拒绝旅游 V2 AI 冲突待确认操作",
)
def cancel_trip_ai_pending(
    trip_id: UUID, pending_id: UUID, request: Request,
):
    tenant_id, owner_id, _actor_id = _scope(request)
    try:
        result = TripAIEditService().cancel_pending_resolution(
            trip_id=str(trip_id), tenant_id=tenant_id, owner_id=owner_id,
            pending_id=str(pending_id),
        )
    except Exception as exc:
        raise _error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={
            "code": "AI_EDIT_PENDING_NOT_FOUND",
            "message": "当前用户范围内没有该待解决操作。",
        })
    status_code = 409 if result.get("status") == "failed" else (
        410 if result.get("status") == "expired" else 200
    )
    return JSONResponse(status_code=status_code, content=result)


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
