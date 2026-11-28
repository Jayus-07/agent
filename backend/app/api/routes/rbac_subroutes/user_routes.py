"""RBAC 用户生命周期与管理路由。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, require_admin_user, require_user_actor
from backend.security.local_jwt import hash_password
from backend.shared.logger import logger
from backend.app.api.routes.rbac_subroutes.routes import router
from backend.app.api.routes.rbac_subroutes.models import (
    _ALLOWED_PLATFORM_ROLES,
    RbacAccess,
    UserUpdateOutcome,
    _MISSING,
    _USERNAME_RE,
    _OPS_USERNAME_PREFIXES,
)
from backend.app.api.routes.rbac_subroutes.dynamic import current_func
from backend.app.api.routes.rbac_subroutes.common import (
    _http_error,
    _rollback,
    _is_concurrency_conflict,
    _validate_role_transition,
    _row_value,
    _db,
    _session_service,
    _actor_user_id,
    _tenant_id,
    _iso,
    _parse_json,
    _valid_new_username,
    _generate_temp_password,
    _write_user_audit,
    _can_operate_super_admin,
    require_rbac_admin,
    require_rbac_access,
)
from backend.app.api.routes.rbac_subroutes.service import (
    update_user_in_transaction,
    _update_department_member_in_transaction,
)

@router.get("/users")
async def list_users(
    request: Request,
    search: str = Query("", max_length=100),
    status: int | None = Query(None, ge=0, le=1),
    dept: str | None = Query(None, max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    access: RbacAccess = Depends(require_rbac_access),
):
    """管理员查看全租户用户；部门主管只能查看被授权部门成员。"""
    search = search.strip()
    tenant_id = _tenant_id(request)
    where = " WHERE u.tenant_id = :tenant_id"
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "tenant_id": tenant_id,
    }
    if access.is_platform_admin:
        if dept:
            where += " AND u.dept = :dept"
            params["dept"] = dept.strip()
    else:
        scoped_departments = list(access.managed_departments)
        if dept and dept.strip() not in scoped_departments:
            raise _http_error("不能查看授权部门范围外的用户", 403)
        if dept:
            scoped_departments = [dept.strip()]
        if not scoped_departments:
            raise _http_error("当前账号没有部门主管授权", 403)
        actor_user_id = _actor_user_id(access.operator)
        if actor_user_id is None:
            raise _http_error("缺少有效的部门主管身份", 403)
        params["actor_user_id"] = actor_user_id
        where += (
            " AND EXISTS (SELECT 1 FROM auth.departments managed "
            "WHERE managed.tenant_id = u.tenant_id AND managed.code = u.dept "
            "AND managed.manager_user_id = :actor_user_id "
            "AND managed.status = 1)"
        )
        department_params = []
        for index, department_code in enumerate(scoped_departments):
            key = f"managed_dept_{index}"
            department_params.append(f":{key}")
            params[key] = department_code
        where += f" AND u.dept IN ({', '.join(department_params)})"
    if search:
        where += (
            " AND (u.username ILIKE :search OR u.real_name ILIKE :search "
            "OR u.dept ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    if status is not None:
        where += " AND u.status = :status"
        params["status"] = status

    async with _db() as db:
        total_result = await db.execute(text(
            f"SELECT COUNT(*) FROM auth.users u{where}"), params)
        total = int(total_result.scalar() or 0)
        rows = (await db.execute(text(
            "SELECT u.id AS user_id, u.username, u.real_name, u.email, u.dept, "
            "u.role AS platform_role, u.status, u.version, "
            "COUNT(DISTINCT s.id) AS session_count "
            "FROM auth.users u "
            "LEFT JOIN auth.sessions s ON s.user_id = u.id "
            "AND s.revoked_at IS NULL AND s.refresh_expires_at > now() "
            f"{where} "
            "GROUP BY u.id, u.username, u.real_name, u.email, u.dept, "
            "u.role, u.status, u.version "
            "ORDER BY u.id LIMIT :limit OFFSET :offset"),
            params)).mappings().all()

    items = []
    for row in rows:
        item = {
            "userId": _row_value(row, "user_id"),
            "username": _row_value(row, "username"),
            "realName": _row_value(row, "real_name") or _row_value(row, "username"),
            "email": _row_value(row, "email") or "",
            "dept": _row_value(row, "dept", ""),
            "status": _row_value(row, "status"),
            "version": int(_row_value(row, "version", 0)),
        }
        if access.is_platform_admin:
            item["platformRole"] = _row_value(row, "platform_role")
            item["sessionCount"] = int(_row_value(row, "session_count", 0) or 0)
        items.append(item)
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


@router.post("/users")
async def create_user(
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """创建用户（P6.1/P6.2）：随机临时密码 + must_change_password=TRUE。

    tenant：请求体可显式指定 tenantId（admin 权限），缺省继承操作者租户
    —— 遵守现有多租户规则，不降级成共享租户。密码走现有 pbkdf2 哈希，
    绝不明文落库；临时密码仅在本响应中出现一次。
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)
    if set(body) & {"csRole", "maxConversations", "enabled", "accepting"}:
        raise _http_error("客服档案请在客服团队页面单独管理", 400)
    tenant_id = _tenant_id(request)
    requested_tenant = str(body.get("tenantId") or "").strip()
    if requested_tenant and requested_tenant != tenant_id:
        raise _http_error("tenantId 与操作者租户不一致（多租户规则禁止跨租户创建）", 403)

    username = str(body.get("username") or "").strip()
    if not _valid_new_username(username):
        raise _http_error(
            "username 必须为 11 位数字，或以 ops_/svc_ 开头的运维账号", 400)
    platform_role = body.get("platformRole") or "viewer"
    if platform_role not in _ALLOWED_PLATFORM_ROLES:
        raise _http_error("platformRole 必须是 viewer/editor/admin/super_admin", 400)
    _validate_role_transition(
        operator.role,
        None,
        platform_role,
        1,
        creating=True,
    )
    real_name = str(body.get("realName") or "").strip()[:50]
    dept = str(body.get("dept") or "").strip()[:50]
    email = str(body.get("email") or "").strip()[:255]

    temp_password = _generate_temp_password()

    async with _db() as db:
        # 部门校验（P9）：与 PATCH 同口径——非空必须命中本租户 active 部门，
        # 禁止创建即挂上一个不存在/跨租户的部门字符串
        if dept:
            available = (await db.execute(text(
                "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
                "WHERE table_schema = 'auth' AND table_name = 'departments'"))).scalar()
            if not available:
                raise _http_error(
                    "部门主数据未初始化（请先应用 041_auth_departments 迁移）", 503)
            dept_ok = (await db.execute(text(
                "SELECT 1 FROM auth.departments "
                "WHERE tenant_id = :tenant_id AND code = :code AND status = 1"),
                {"tenant_id": tenant_id, "code": dept})).first()
            if dept_ok is None:
                raise _http_error(
                    f"部门 '{dept}' 不存在或未启用（须为本租户 active 部门）", 400)
        exists = (await db.execute(text(
            "SELECT 1 FROM auth.users WHERE username = :u"),
            {"u": username})).first()
        if exists is not None:
            raise _http_error("用户名已存在", 409)
        row = (await db.execute(text(
            "INSERT INTO auth.users "
            "(username, password_hash, real_name, dept, role, status, tenant_id, "
            "email, must_change_password) VALUES "
            "(:username, :ph, :real_name, :dept, :role, 1, :tenant_id, "
            ":email, TRUE) "
            "RETURNING id, username, real_name, dept, role, status, tenant_id, "
            "created_at"),
            {
                "username": username,
                "ph": hash_password(temp_password),
                "real_name": real_name,
                "dept": dept,
                "role": platform_role,
                "tenant_id": tenant_id,
                "email": email,
            })).mappings().first()
        await db.commit()

    user_id = _row_value(row, "id")
    _audit_after = {
        "action": "create",
        "username": username,
        "platformRole": platform_role,
        "mustChangePassword": True,
    }
    async with _db() as db:
        await _write_user_audit(
            db, tenant_id=tenant_id,
            actor_user_id=_actor_user_id(operator),
            target_user_id=int(user_id),
            action="user.create", after_state=_audit_after)
        await db.commit()

    return {
        "userId": user_id,
        "username": username,
        "realName": _row_value(row, "real_name", real_name),
        "platformRole": platform_role,
        "status": 1,
        "tenantId": _row_value(row, "tenant_id", tenant_id),
        "mustChangePassword": True,
        # 临时密码明文仅此一次返回，前端负责展示给管理员转交用户
        "tempPassword": temp_password,
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """用户详情（P6.1）：基本资料、平台角色和会话/登录信息。"""
    del operator
    tenant_id = _tenant_id(request)
    async with _db() as db:
        row = (await db.execute(text(
            "SELECT id, username, real_name, dept, email, role, status, "
            "version, tenant_id, created_at, must_change_password "
            "FROM auth.users WHERE id = :uid AND tenant_id = :tenant_id"),
            {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
        if row is None:
            raise _http_error("用户不存在", 404)
        session_stat = (await db.execute(text(
            "SELECT COUNT(*) AS active_sessions, "
            "MAX(created_at) AS last_login_at FROM auth.sessions "
            "WHERE user_id = :uid AND revoked_at IS NULL"),
            {"uid": user_id})).mappings().first()

    return {
        "userId": _row_value(row, "id"),
        "username": _row_value(row, "username"),
        "realName": _row_value(row, "real_name"),
        "email": _row_value(row, "email", ""),
        "dept": _row_value(row, "dept", ""),
        "tenantId": _row_value(row, "tenant_id"),
        "platformRoles": [_row_value(row, "role", "viewer")],
        "status": int(_row_value(row, "status", 1)),
        "version": int(_row_value(row, "version", 0)),
        "mustChangePassword": bool(_row_value(row, "must_change_password", False)),
        "createdAt": _iso(_row_value(row, "created_at")),
        "lastLoginAt": _iso(_row_value(session_stat, "last_login_at")),
        "activeSessionCount": int(
            _row_value(session_stat, "active_sessions", 0) or 0),
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    access: RbacAccess = Depends(require_rbac_access),
):
    """管理员修改平台用户；部门主管仅能修改本部门基础资料和账号状态。"""
    body = await request.json()
    tenant_id = _tenant_id(request)
    db = None
    try:
        async with _db() as db:
            if access.is_platform_admin:
                if not isinstance(body, dict):
                    raise _http_error("请求体必须是 JSON 对象", 400)
                if set(body) & {"csRole", "maxConversations", "enabled", "accepting"}:
                    raise _http_error("客服档案请在客服团队页面单独管理", 400)
                outcome = await current_func("update_user_in_transaction", update_user_in_transaction)(
                    db, user_id=user_id, body=body,
                    operator=access.operator, tenant_id=tenant_id,
                )
            else:
                outcome = await current_func("_update_department_member_in_transaction", _update_department_member_in_transaction)(
                    db, user_id=user_id, body=body, access=access,
                    tenant_id=tenant_id,
                )
            await db.commit()
    except HTTPException:
        await _rollback(db)
        raise
    except Exception as exc:
        await _rollback(db)
        if _is_concurrency_conflict(exc):
            raise HTTPException(status_code=409, detail="RBAC 更新发生并发冲突，请重试") from exc
        logger.exception("[RBAC] 用户更新事务失败 user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="RBAC 更新事务失败") from exc

    deleted = _session_service.clear_redis_for_sessions(outcome.revoked_sessions)
    return {**outcome.user, "revokedSessionCount": len(outcome.revoked_sessions),
            "redisKeysDeleted": deleted}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """重置密码（P6.3）：生成随机临时密码，强制改密 + 立即吊销全部会话。"""
    tenant_id = _tenant_id(request)
    temp_password = _generate_temp_password()
    async with _db() as db:
        row = (await db.execute(text(
            "SELECT id, username, role FROM auth.users "
            "WHERE id = :uid AND tenant_id = :tenant_id FOR UPDATE"),
            {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
        if row is None:
            raise _http_error("用户不存在", 404)
        if not _can_operate_super_admin(operator.role, row.get("role")):
            raise _http_error("仅 super_admin 可重置 super_admin 账号", 403)
        await db.execute(text(
            "UPDATE auth.users SET password_hash = :ph, "
            "must_change_password = TRUE, updated_at = now() WHERE id = :uid"),
            {"ph": hash_password(temp_password), "uid": user_id})
        revoked = await _session_service.revoke_user_sessions(
            db, user_id, reason="password_reset"
        )
        await _write_user_audit(
            db, tenant_id=tenant_id,
            actor_user_id=_actor_user_id(operator),
            target_user_id=user_id,
            action="user.reset_password",
            after_state={"mustChangePassword": True,
                         "revokedSessions": len(revoked)})
        await db.commit()
    deleted = _session_service.clear_redis_for_sessions(revoked)
    return {
        "userId": user_id,
        "username": _row_value(row, "username"),
        "mustChangePassword": True,
        "revokedSessionCount": len(revoked),
        "redisKeysDeleted": deleted,
        # 临时密码明文仅此一次返回
        "tempPassword": temp_password,
    }


@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """强制下线（P6.2）：复用 SessionService.revoke_user_sessions，不建第二套。"""
    tenant_id = _tenant_id(request)
    async with _db() as db:
        row = (await db.execute(text(
            "SELECT id, username, role FROM auth.users "
            "WHERE id = :uid AND tenant_id = :tenant_id"),
            {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
        if row is None:
            raise _http_error("用户不存在", 404)
        if not _can_operate_super_admin(operator.role, row.get("role")):
            raise _http_error("仅 super_admin 可强制下线 super_admin 账号", 403)
        revoked = await _session_service.revoke_user_sessions(
            db, user_id, reason="force_logout"
        )
        await _write_user_audit(
            db, tenant_id=tenant_id,
            actor_user_id=_actor_user_id(operator),
            target_user_id=user_id,
            action="user.force_logout",
            after_state={"revokedSessions": len(revoked)})
        await db.commit()
    deleted = _session_service.clear_redis_for_sessions(revoked)
    return {
        "userId": user_id,
        "revokedSessionCount": len(revoked),
        "redisKeysDeleted": deleted,
    }

