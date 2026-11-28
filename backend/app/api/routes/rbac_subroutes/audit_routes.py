"""RBAC 审计日志路由。"""
from __future__ import annotations

from typing import Any
from fastapi import Depends, Query, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.app.api.routes.rbac_subroutes.routes import router
from backend.app.api.routes.rbac_subroutes.dynamic import current_func
from backend.app.api.routes.rbac_subroutes.common import (
    _http_error,
    _db,
    _tenant_id,
    _audit_item,
    require_rbac_admin,
)

@router.get("/audit")
async def list_audit(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user_id: str = Query("", max_length=64),
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """分页返回 RBAC 审计记录。"""
    del operator
    tenant_id = _tenant_id(request)
    user_id = user_id.strip()
    target_user_id: int | None = None
    if user_id:
        if not user_id.isdigit() or int(user_id) < 1:
            raise _http_error("user_id 必须是正整数", 400)
        target_user_id = int(user_id)
    where = "WHERE a.tenant_id = :tenant_id"
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if target_user_id is not None:
        where += " AND a.target_user_id = :user_id"
        params["user_id"] = target_user_id

    async with _db() as db:
        total = int((await db.execute(text(
            f"SELECT COUNT(*) FROM auth.rbac_audits a {where}"), params)).scalar() or 0)
        rows = (await db.execute(text(
            f"SELECT a.id, a.tenant_id, a.actor_user_id, a.target_user_id, "
            f"actor.username AS actor_username, target.username AS target_username, "
            f"a.action, a.before_state, a.after_state, a.result, a.created_at "
            f"FROM auth.rbac_audits a "
            "LEFT JOIN auth.users actor ON actor.id = a.actor_user_id "
            "AND actor.tenant_id = a.tenant_id "
            "LEFT JOIN auth.users target ON target.id = a.target_user_id "
            "AND target.tenant_id = a.tenant_id "
            f"{where} "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"),
            params)).mappings().all()
    return {
        "items": [_audit_item(row) for row in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }

