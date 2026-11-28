"""RBAC 客服团队协同路由。"""
from __future__ import annotations

import json
import uuid

from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.shared.logger import logger
from backend.security.session_service import SessionRef
from backend.app.api.routes.rbac_subroutes.routes import router
from backend.app.api.routes.rbac_subroutes.models import (
    RbacAccess,
    UserUpdateOutcome,
    _MISSING,
)
from backend.app.api.routes.rbac_subroutes.dynamic import current_func
from backend.app.api.routes.rbac_subroutes.common import (
    _validate_body,
    _http_error,
    _rollback,
    _is_concurrency_conflict,
    _stable_display_name,
    _row_value,
    _load_cs_agent,
    _db,
    _session_service,
    _actor_user_id,
    _tenant_id,
    _cs_role,
    _cs_public,
    _state,
    _merge_agent,
    _is_customer_service_department,
    require_rbac_admin,
    require_rbac_access,
)
from backend.app.api.routes.rbac_subroutes.service import update_user_in_transaction

@router.get("/customer-service/team")
async def list_customer_service_team(
    request: Request,
    search: str = Query("", max_length=100),
    status: int | None = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """仅列出本租户客服部门成员及其客服档案。"""
    del operator
    tenant_id = _tenant_id(request)
    search = search.strip()
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    where = (
        " WHERE u.tenant_id = :tenant_id AND d.status = 1 "
        "AND d.department_type = 'customer_service'"
    )
    if search:
        where += " AND (u.username ILIKE :search OR u.real_name ILIKE :search)"
        params["search"] = f"%{search}%"
    if status is not None:
        where += " AND u.status = :status"
        params["status"] = status

    async with _db() as db:
        department = (await db.execute(text(
            "SELECT code, name FROM auth.departments "
            "WHERE tenant_id = :tenant_id AND status = 1 "
            "AND department_type = 'customer_service'"),
            {"tenant_id": tenant_id})).mappings().first()
        if department is None:
            return {
                "items": [], "total": 0, "page": page,
                "pageSize": page_size, "department": None,
            }
        total = int((await db.execute(text(
            "SELECT COUNT(*) FROM auth.users u "
            "JOIN auth.departments d ON d.tenant_id = u.tenant_id "
            "AND d.code = u.dept" + where), params)).scalar() or 0)
        rows = (await db.execute(text(
            "SELECT u.id AS user_id, u.username, u.real_name, u.email, "
            "u.dept, u.status, u.version, a.agent_id, a.display_name, "
            "a.role AS cs_role, a.max_conversations, a.enabled, a.accepting "
            "FROM auth.users u "
            "JOIN auth.departments d ON d.tenant_id = u.tenant_id "
            "AND d.code = u.dept "
            "LEFT JOIN customer_service.cs_agents a "
            "ON a.auth_user_id = CAST(u.id AS VARCHAR) "
            "AND a.tenant_id = u.tenant_id "
            + where + " ORDER BY u.id LIMIT :limit OFFSET :offset"),
            params)).mappings().all()

    items = []
    for row in rows:
        agent = None
        if _row_value(row, "agent_id"):
            agent = {
                "agent_id": _row_value(row, "agent_id"),
                "display_name": _row_value(row, "display_name"),
                "role": _row_value(row, "cs_role"),
                "max_conversations": _row_value(row, "max_conversations"),
                "enabled": _row_value(row, "enabled"),
                "accepting": _row_value(row, "accepting"),
            }
        items.append({
            "userId": _row_value(row, "user_id"),
            "username": _row_value(row, "username"),
            "realName": _row_value(row, "real_name") or _row_value(row, "username"),
            "email": _row_value(row, "email") or "",
            "dept": _row_value(row, "dept"),
            "status": int(_row_value(row, "status", 1)),
            "version": int(_row_value(row, "version", 0)),
            "csAgent": _cs_public(agent),
        })
    return {
        "items": items, "total": total, "page": page, "pageSize": page_size,
        "department": {"code": department["code"], "name": department["name"]},
    }


@router.patch("/customer-service/team/{user_id}")
async def update_customer_service_member(
    user_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """独立维护客服部成员客服角色、容量和接单状态。"""
    body = await request.json()
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)
    allowed_fields = {
        "version", "csRole", "maxConversations", "enabled", "accepting",
    }
    if set(body) - allowed_fields:
        raise _http_error("客服团队接口只接受客服档案字段", 400)
    if not set(body) - {"version"}:
        raise _http_error("请至少修改一项客服档案", 400)
    parsed = _validate_body(body, require_version=True)
    tenant_id = _tenant_id(request)
    db = None
    revoked_sessions: list[SessionRef] = []
    try:
        async with _db() as db:
            current = (await db.execute(text(
                "SELECT u.id, u.username, u.real_name, u.email, u.dept, "
                "u.status, u.version "
                "FROM auth.users u JOIN auth.departments d "
                "ON d.tenant_id = u.tenant_id AND d.code = u.dept "
                "WHERE u.id = :user_id AND u.tenant_id = :tenant_id "
                "AND u.status = 1 AND d.status = 1 "
                "AND d.department_type = 'customer_service' "
                "FOR UPDATE OF u, d"), {
                    "user_id": user_id, "tenant_id": tenant_id,
                })).mappings().first()
            if current is None:
                raise _http_error("仅可配置本租户启用客服部门中的在职账号", 404)
            current_version = int(_row_value(current, "version", 0))
            if parsed["version"] != current_version:
                raise _http_error("用户版本冲突，请刷新后重试", 409)
            current_agent = await _load_cs_agent(
                db, tenant_id=tenant_id, user_id=user_id
            )
            requested_role = parsed["cs_role"]
            if requested_role is _MISSING:
                requested_role = _row_value(current_agent, "role", "agent")
            if requested_role is None and current_agent is None:
                await db.commit()
                return {
                    "userId": user_id,
                    "version": current_version,
                    "csAgent": None,
                    "revokedSessionCount": 0,
                }

            if requested_role is None:
                new_values = {"enabled": False, "accepting": False}
                role = _row_value(current_agent, "role", "agent")
                max_conversations = int(
                    _row_value(current_agent, "max_conversations", 10) or 10
                )
            else:
                role = requested_role
                max_conversations = (
                    int(_row_value(current_agent, "max_conversations", 10) or 10)
                    if parsed["max_conversations"] is _MISSING
                    else parsed["max_conversations"]
                )
                enabled = (
                    bool(_row_value(current_agent, "enabled", True))
                    if parsed["enabled"] is _MISSING else parsed["enabled"]
                )
                accepting = (
                    bool(_row_value(current_agent, "accepting", True))
                    if parsed["accepting"] is _MISSING else parsed["accepting"]
                )
                if not enabled and parsed["accepting"] is _MISSING:
                    accepting = False
                if accepting and not enabled:
                    raise _http_error("停用的客服档案不能处于可接单状态", 400)
                new_values = {
                    "enabled": enabled,
                    "accepting": accepting,
                }

            before_state = _state(current, current_agent)
            if current_agent is None:
                agent_id = f"cs-{uuid.uuid4().hex}"
                agent_values = {
                    "agent_id": agent_id,
                    "tenant_id": tenant_id,
                    "auth_user_id": str(user_id),
                    "display_name": _stable_display_name(current, user_id),
                    "role": role,
                    "max_conversations": max_conversations,
                    "enabled": new_values["enabled"],
                    "accepting": new_values["accepting"],
                }
                await db.execute(text(
                    "INSERT INTO customer_service.cs_agents "
                    "(agent_id, tenant_id, auth_user_id, display_name, role, "
                    "max_conversations, enabled, accepting) VALUES "
                    "(:agent_id, :tenant_id, :auth_user_id, :display_name, "
                    ":role, :max_conversations, :enabled, :accepting)"),
                    agent_values)
                after_agent = agent_values
            else:
                agent_id = _row_value(current_agent, "agent_id")
                await db.execute(text(
                    "UPDATE customer_service.cs_agents SET role = :role, "
                    "max_conversations = :max_conversations, enabled = :enabled, "
                    "accepting = :accepting, updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND agent_id = :agent_id"), {
                        "role": role,
                        "max_conversations": max_conversations,
                        "enabled": new_values["enabled"],
                        "accepting": new_values["accepting"],
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                    })
                after_agent = {
                    **dict(current_agent),
                    "role": role,
                    "max_conversations": max_conversations,
                    **new_values,
                }

            updated = (await db.execute(text(
                "UPDATE auth.users SET version = version + 1, updated_at = now() "
                "WHERE id = :user_id AND tenant_id = :tenant_id "
                "AND version = :version RETURNING version"), {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "version": current_version,
                })).mappings().first()
            if updated is None:
                raise _http_error("用户版本冲突，请刷新后重试", 409)

            after_state = _state(current, after_agent)
            cs_changes = {}
            if before_state["csRole"] != after_state["csRole"]:
                cs_changes["role"] = after_state["csRole"]
            for key in ("maxConversations", "enabled", "accepting"):
                if before_state.get(key) != after_state.get(key):
                    cs_changes[key] = after_state.get(key)
            after_state["csChanges"] = cs_changes
            await db.execute(text(
                "INSERT INTO auth.rbac_audits "
                "(tenant_id, actor_user_id, target_user_id, action, before_state, "
                "after_state, result) VALUES (:tenant_id, :actor_user_id, "
                ":target_user_id, 'user.update', CAST(:before_state AS JSONB), "
                "CAST(:after_state AS JSONB), 'success')"), {
                    "tenant_id": tenant_id,
                    "actor_user_id": _actor_user_id(operator),
                    "target_user_id": user_id,
                    "before_state": json.dumps(before_state, ensure_ascii=False),
                    "after_state": json.dumps(after_state, ensure_ascii=False),
                })
            if before_state["csRole"] != after_state["csRole"] or (
                before_state.get("enabled") != after_state.get("enabled")
            ):
                revoked_sessions = await _session_service.revoke_user_sessions(
                    db, user_id, reason="rbac_changed"
                )
            await db.commit()
    except HTTPException:
        await _rollback(db)
        raise
    except Exception as exc:
        await _rollback(db)
        if _is_concurrency_conflict(exc):
            raise HTTPException(
                status_code=409, detail="客服档案更新发生并发冲突，请重试"
            ) from exc
        logger.exception(
            "[RBAC] 客服档案更新事务失败 user_id=%s", user_id
        )
        raise HTTPException(status_code=500, detail="客服档案更新失败") from exc

    deleted = _session_service.clear_redis_for_sessions(revoked_sessions)
    return {
        "userId": user_id,
        "version": int(_row_value(updated, "version", current_version + 1)),
        "csAgent": _cs_public(after_agent),
        "revokedSessionCount": len(revoked_sessions),
        "redisKeysDeleted": deleted,
    }

