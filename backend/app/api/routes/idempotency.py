"""幂等操作状态查询 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.identity import require_identity
from backend.shared.idempotency import (
    IdempotencyUnavailable,
    PostgresIdempotencyResultStore,
)


router = APIRouter(tags=["幂等"])


def _store() -> PostgresIdempotencyResultStore:
    return PostgresIdempotencyResultStore()


@router.get("/idempotency/operations/{client_key}")
async def get_operation_status(client_key: str, request: Request) -> dict[str, Any]:
    """只允许原租户/操作者查询，并且只返回安全状态摘要。"""
    identity = require_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "幂等状态查询需要可信租户身份")
    key = client_key.strip()
    if not key:
        raise HTTPException(
            400,
            detail={"code": "INVALID_PARAM", "message": "client_key 不能为空"},
        )
    try:
        result = _store().get_status(
            tenant_id=identity.tenant_id,
            actor_id=identity.user_id,
            client_key=key,
        )
    except IdempotencyUnavailable as exc:
        raise HTTPException(503, "幂等状态暂不可用") from exc
    if result is None:
        raise HTTPException(
            404,
            detail={"code": "NOT_FOUND", "message": "未找到该幂等操作"},
        )
    # 即使底层适配器未来扩展字段，也只允许固定白名单出现在响应中。
    public_fields = (
        "client_key", "operation", "status", "attempt", "error_code",
        "has_result", "created_at", "updated_at", "expires_at",
    )
    return {field: result[field] for field in public_fields if field in result}
