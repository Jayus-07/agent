"""模型价格权威表治理 API。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    resolve_operator_role,
    run_governance_mutation,
)
from backend.infra.llm.price_governance import (
    PostgresPriceGovernanceRepository,
    PriceGovernanceUnavailable,
)
from backend.app.api.routes.price_dto import validate_price_rows


router = APIRouter(tags=["价格治理"])


class PriceImportRequest(BaseModel):
    version: str = Field(..., min_length=1, max_length=128)
    source: str = Field(..., min_length=1, max_length=500)
    effective_from: datetime | None = None
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=5000)


class PriceReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field("", max_length=1000)


class PriceCanaryRequest(BaseModel):
    action: Literal["start", "complete"]


@router.post("/admin/model-prices/imports/validate")
async def validate_model_price_import(
    body: PriceImportRequest,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """服务端预校验价格导入内容；只返回规范化结果，不写入数据库。"""
    del operator
    result = validate_price_rows(body.rows)
    return {
        "version": body.version,
        "source": body.source,
        **result,
    }


def _repo() -> PostgresPriceGovernanceRepository:
    return PostgresPriceGovernanceRepository()


def _handle_storage(exc: PriceGovernanceUnavailable) -> HTTPException:
    return HTTPException(503, "价格治理暂不可用")


@router.get("/admin/model-prices/versions")
async def list_model_price_versions(
    limit: int = Query(100, ge=1, le=200),
    operator: OperatorIdentity = Depends(resolve_operator_role),
) -> dict[str, Any]:
    if operator.role not in {"viewer", "editor", "admin"}:
        raise HTTPException(403, "无权查看价格版本")
    try:
        return {"items": _repo().list_versions(limit)}
    except PriceGovernanceUnavailable as exc:
        raise _handle_storage(exc) from exc


@router.post("/admin/model-prices/imports")
@router.post("/admin/model-prices/import", include_in_schema=False)
async def import_model_price_version(
    body: PriceImportRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    def persist() -> dict[str, Any]:
        return _repo().import_version(
            body.rows,
            version=body.version,
            source=body.source,
            imported_by=operator.actor,
            effective_from=body.effective_from,
        )

    try:
        return run_governance_mutation(
            request,
            operator,
            "model_price.import",
            body.model_dump(mode="json"),
            persist,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PriceGovernanceUnavailable as exc:
        raise _handle_storage(exc) from exc


@router.post("/admin/model-prices/versions/{version}/reviews")
@router.post("/admin/model-prices/versions/{version}/review", include_in_schema=False)
async def review_model_price_version(
    version: str,
    body: PriceReviewRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    def persist() -> dict[str, Any]:
        return _repo().review_version(
            version,
            reviewer=operator.actor,
            decision=body.decision,
            reason=body.reason,
        )

    try:
        return run_governance_mutation(
            request,
            operator,
            "model_price.review",
            {"version": version, **body.model_dump(mode="json")},
            persist,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PriceGovernanceUnavailable as exc:
        raise _handle_storage(exc) from exc


@router.post("/admin/model-prices/versions/{version}/canary")
async def canary_model_price_version(
    version: str,
    body: PriceCanaryRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    def persist() -> dict[str, Any]:
        return _repo().canary_version(version, action=body.action)

    try:
        return run_governance_mutation(
            request,
            operator,
            "model_price.canary",
            {"version": version, **body.model_dump(mode="json")},
            persist,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PriceGovernanceUnavailable as exc:
        raise _handle_storage(exc) from exc
