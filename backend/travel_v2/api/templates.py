from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.app.api.identity import require_identity
from backend.travel_v2.db import TravelV2PersistenceError
from backend.travel_v2.models.template import TemplateCreateRequest, TemplateUpdateRequest
from backend.travel_v2.services.template_service import (
    TemplateConflict,
    TemplateNotFound,
    TemplateService,
    TemplateVersionConflict,
)
from backend.travel_v2.services.trip_edit_service import IdempotencyConflict

router = APIRouter(tags=["旅游 V2 模板"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, TemplateNotFound):
        return HTTPException(status_code=404, detail={"code": "TEMPLATE_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, (TemplateConflict, TemplateVersionConflict, IdempotencyConflict)):
        return HTTPException(status_code=409, detail={"code": "TEMPLATE_CONFLICT", "message": str(exc)})
    if isinstance(exc, TravelV2PersistenceError):
        return HTTPException(status_code=503, detail={
            "code": "TRAVEL_V2_PERSISTENCE_UNAVAILABLE",
            "message": "模板或行程没有保存，请稍后重试",
        })
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail={"code": "INVALID_TEMPLATE", "message": str(exc)})
    raise exc


@router.get("/travel/v2/templates", summary="浏览已发布旅游模板")
def list_templates(
    request: Request,
    destination: str | None = Query(default=None, max_length=160),
    limit: int = Query(30, ge=1, le=100),
):
    require_identity(request)
    try:
        return {"templates": TemplateService().list_published(
            destination=destination, limit=limit,
        )}
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/travel/v2/templates/{template_key}", summary="查看已发布模板详情")
def get_template(template_key: str, request: Request):
    require_identity(request)
    try:
        return TemplateService().get_published(template_key)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/templates", summary="平台管理员创建旅游模板", status_code=201)
def create_template(
    payload: TemplateCreateRequest,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    if operator.kind != "user":
        raise HTTPException(status_code=403, detail="仅平台管理员用户可维护旅游模板")
    try:
        return TemplateService().create_template(
            payload, actor_id=operator.actor.removeprefix("user:"),
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.put("/travel/v2/templates/{template_id}", summary="平台管理员更新当前模板版本")
def update_template(
    template_id: str,
    payload: TemplateUpdateRequest,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    if operator.kind != "user":
        raise HTTPException(status_code=403, detail="仅平台管理员用户可维护旅游模板")
    try:
        return TemplateService().update_template(template_id, payload)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/templates/{template_id}/publish", summary="平台管理员发布旅游模板")
def publish_template(
    template_id: str,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    if operator.kind != "user":
        raise HTTPException(status_code=403, detail="仅平台管理员用户可维护旅游模板")
    try:
        return TemplateService().publish(template_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/travel/v2/templates/{template_key}/copy", summary="复制模板为全新个人行程", status_code=201)
def copy_template(
    template_key: str,
    request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
):
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(status_code=403, detail="旅游 V2 行程需要完整的用户与租户身份")
    try:
        result = TemplateService().copy_to_trip(
            template_key, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=owner_id, idempotency_key=idempotency_key,
        )
        if result.pop("replayed", False):
            response.status_code = 200
        return result
    except Exception as exc:
        raise _error(exc) from exc
