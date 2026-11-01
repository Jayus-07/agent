"""travel_v2/api/auto_optimize.py — 自动优选端点（收编重构 U3，READ 编排）

POST /travel/v2/trips/{trip_id}/auto-optimize：聚合查询 → 择优建议清单。
只读编排：建议不是已选动作，逐条选入走既有 selections 写端点（写路径
唯一），确认权在用户。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.identity import require_identity
from backend.travel_v2.services.auto_optimize_service import (
    build_optimization_suggestions,
)
from backend.travel_v2.services.trip_service import TripNotFound, TripService

router = APIRouter(tags=["旅游 V2 自动优选"])


@router.post(
    "/travel/v2/trips/{trip_id}/auto-optimize",
    summary="自动优选建议（餐饮/住宿聚合择优；只读编排，选入走既有端点）",
)
def auto_optimize(trip_id: str, request: Request):
    identity = require_identity(request)
    tenant_id = (identity.tenant_id or "").strip()
    owner_id = (identity.user_id or "").strip()
    if not tenant_id or not owner_id:
        raise HTTPException(status_code=403, detail={
            "code": "TRAVEL_V2_IDENTITY_INCOMPLETE",
            "message": "自动优选需要完整的用户与租户身份",
        })
    try:
        trip = TripService().get_trip(
            trip_id, tenant_id=tenant_id, owner_id=owner_id)
    except TripNotFound:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_FOUND", "message": "行程不存在",
        }) from None
    return build_optimization_suggestions(
        trip, tenant_id=tenant_id, owner_id=owner_id)
