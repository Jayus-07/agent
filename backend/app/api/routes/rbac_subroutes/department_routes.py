"""RBAC 部门管理路由。"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.shared.logger import logger
from backend.app.api.routes.rbac_subroutes.routes import router
from backend.app.api.routes.rbac_subroutes.models import (
    RbacAccess,
    _DEPARTMENT_CODE_RE,
    _MISSING,
)
from backend.app.api.routes.rbac_subroutes.dynamic import current_func
from backend.app.api.routes.rbac_subroutes.common import (
    _is_concurrency_conflict,
    _http_error,
    _db,
    _actor_user_id,
    _tenant_id,
    _department_public,
    _department_audit_ready,
    _write_department_audit,
    _rollback,
    require_rbac_admin,
    require_rbac_access,
)
from backend.app.api.routes.rbac_subroutes.service import _change_department

@router.get("/departments")
async def list_departments(
    request: Request,
    include_inactive: bool = Query(False),
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """列出本租户部门；用户归属下拉默认只返回启用项。

    禁止前端硬编码部门清单——主数据权威在 auth.departments（041 迁移）。
    未就绪（迁移未应用）时返回空列表 + warning，管理端下拉自然为空，
    不阻塞其他用户管理功能（读接口 fail-soft，写校验仍 fail-closed）。
    """
    del operator
    tenant_id = _tenant_id(request)
    async with _db() as db:
        available = (await db.execute(text(
            "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
            "WHERE table_schema = 'auth' AND table_name = 'departments'"))).scalar()
        if not available:
            logger.warning("[RBAC] auth.departments 未就绪（041 迁移未应用），部门列表为空")
            return {"items": []}
        rows = (await db.execute(text(
            "SELECT d.code, d.name, d.status, d.version, d.updated_at, "
            "d.department_type, d.manager_user_id, "
            "m.username AS manager_username, m.real_name AS manager_real_name, "
            "COUNT(u.id)::INTEGER AS member_count "
            "FROM auth.departments d "
            "LEFT JOIN auth.users u ON u.tenant_id = d.tenant_id "
            "AND u.dept = d.code "
            "LEFT JOIN auth.users m ON m.tenant_id = d.tenant_id "
            "AND m.id = d.manager_user_id "
            "WHERE d.tenant_id = :tenant_id "
            "AND (:include_inactive OR d.status = 1) "
            "GROUP BY d.code, d.name, d.status, d.version, d.updated_at, "
            "d.department_type, d.manager_user_id, m.username, m.real_name "
            "ORDER BY d.status DESC, d.name, d.code"),
            {"tenant_id": tenant_id, "include_inactive": include_inactive}
        )).mappings().all()
    return {"items": [_department_public(row) for row in rows]}


@router.post("/departments", status_code=201)
async def create_department(
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """创建本租户部门；租户来自已验证身份，创建记录写入部门审计表。"""
    body = await request.json()
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)

    tenant_id = _tenant_id(request)
    requested_tenant = str(body.get("tenantId") or "").strip()
    if requested_tenant and requested_tenant != tenant_id:
        raise _http_error("tenantId 与操作者租户不一致（多租户规则禁止跨租户创建）", 403)

    raw_code = body.get("code")
    raw_name = body.get("name")
    department_type = body.get("departmentType", "general")
    if not isinstance(raw_code, str) or not isinstance(raw_name, str):
        raise _http_error("code 和 name 必须是字符串", 400)
    code = raw_code.strip().lower()
    name = raw_name.strip()
    if not _DEPARTMENT_CODE_RE.fullmatch(code):
        raise _http_error(
            "部门编码须为 1-50 位小写字母开头，后续只能包含小写字母、数字、_ 或 -",
            400,
        )
    if code == "all":
        raise _http_error("部门编码 all 为系统保留值，不能使用", 400)
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise _http_error("部门名称不能为空，且不能超过 100 个字符", 400)
    if not isinstance(department_type, str) or department_type not in {
        "general", "customer_service",
    }:
        raise _http_error("departmentType 必须是 general/customer_service", 400)

    db = None
    try:
        async with _db() as db:
            available = (await db.execute(text(
                "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
                "WHERE table_schema = 'auth' AND table_name = 'departments'"
            ))).scalar()
            audit_available = await _department_audit_ready(db)
            if not available:
                raise _http_error(
                    "部门主数据未初始化（请先应用 041_auth_departments 迁移）", 503)
            if not audit_available:
                raise _http_error(
                    "部门审计表未初始化（请先应用 102_rbac_department_audits 迁移）", 503)

            duplicate = (await db.execute(text(
                "SELECT 1 FROM auth.departments "
                "WHERE tenant_id = :tenant_id AND code = :code"
            ), {"tenant_id": tenant_id, "code": code})).first()
            if duplicate is not None:
                raise _http_error("该部门编码已存在", 409)

            if department_type == "customer_service":
                existing_customer_service = (await db.execute(text(
                    "SELECT 1 FROM auth.departments WHERE tenant_id = :tenant_id "
                    "AND status = 1 AND department_type = 'customer_service' "
                    "LIMIT 1 FOR UPDATE"),
                    {"tenant_id": tenant_id})).first()
                if existing_customer_service:
                    raise _http_error("本租户已有启用的客服部门", 409)

            row = (await db.execute(text(
                "INSERT INTO auth.departments "
                "(tenant_id, code, name, status, department_type) "
                "VALUES (:tenant_id, :code, :name, 1, :department_type) "
                "RETURNING code, name, status, version, updated_at, "
                "department_type, manager_user_id"
            ), {"tenant_id": tenant_id, "code": code, "name": name,
                "department_type": department_type})).mappings().first()
            if row is None:
                raise RuntimeError("部门创建未返回记录")

            after_state = _department_public(row)
            await _write_department_audit(
                db, tenant_id=tenant_id, operator=operator, code=code,
                name=name, action="department.create", before_state={},
                after_state=after_state,
            )
            await db.commit()
    except HTTPException:
        await _rollback(db)
        raise
    except Exception as exc:
        await _rollback(db)
        if _is_concurrency_conflict(exc):
            raise HTTPException(
                status_code=409,
                detail="部门创建发生并发冲突，请刷新后重试",
            ) from exc
        logger.exception("[RBAC] 部门创建事务失败 tenant_id=%s", tenant_id)
        raise HTTPException(status_code=500, detail="部门创建失败") from exc

    return _department_public(row)


@router.patch("/departments/{code}")
async def update_department(
    code: str,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    body = await request.json()
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)
    version = body.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _http_error("部门版本号必须是整数", 400)
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise _http_error("部门名称必须是字符串", 400)
    raw_status = body.get("status")
    if raw_status is not None and (isinstance(raw_status, bool) or not isinstance(raw_status, int)):
        raise _http_error("部门状态必须是整数", 400)
    department_type = body.get("departmentType")
    if department_type is not None and not isinstance(department_type, str):
        raise _http_error("departmentType 必须是字符串", 400)
    manager_user_id = body.get("managerUserId", _MISSING)
    return await current_func("_change_department", _change_department)(
        code=code, request=request, operator=operator, version=version,
        name=name.strip() if name is not None else None, status=raw_status,
        department_type=department_type,
        manager_user_id=manager_user_id,
    )


@router.delete("/departments/{code}")
async def deactivate_department(
    code: str,
    request: Request,
    version: int = Query(..., ge=0),
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """停用部门而非物理删除，保护历史审计和现有授权引用。"""
    return await current_func("_change_department", _change_department)(
        code=code, request=request, operator=operator, version=version,
        status=0,
    )

