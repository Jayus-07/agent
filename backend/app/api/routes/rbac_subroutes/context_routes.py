"""RBAC 上下文路由。"""
from __future__ import annotations

from fastapi import Depends, Request

from backend.app.api.deps import OperatorIdentity, require_user_actor
from backend.app.api.routes.rbac_subroutes.routes import router
from backend.app.api.routes.rbac_subroutes.models import RbacAccess
from backend.app.api.routes.rbac_subroutes.common import (
    require_rbac_access,
    _tenant_id,
    _db,
    _http_error,
    _manager_departments,
    _actor_user_id,
)

@router.get("/context")
async def access_context(
    request: Request,
    access: RbacAccess = Depends(require_rbac_access),
):
    """返回当前用户在访问控制页内可用的服务端授权范围。"""
    tenant_id = _tenant_id(request)
    if access.is_platform_admin:
        return {
            "isPlatformAdmin": True,
            "managedDepartments": [],
        }
    actor_user_id = _actor_user_id(access.operator)
    if actor_user_id is None:
        raise _http_error("当前账号没有部门主管授权", 403)
    async with _db() as db:
        departments = await _manager_departments(
            db, tenant_id=tenant_id, user_id=actor_user_id
        )
    return {"isPlatformAdmin": False, "managedDepartments": departments}

