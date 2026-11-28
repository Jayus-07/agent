"""RBAC 核心领域事务处理服务。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity
from backend.security.session_service import SessionRef
from backend.shared.logger import logger
from backend.app.api.routes.rbac_subroutes.models import (
    RbacAccess,
    UserUpdateOutcome,
    _ALLOWED_PLATFORM_ROLES,
    _ALLOWED_CS_ROLES,
    _DEPARTMENT_CODE_RE,
    _MISSING,
    _USERNAME_RE,
    _OPS_USERNAME_PREFIXES,
)
from backend.app.api.routes.rbac_subroutes.dynamic import current_func
from backend.app.api.routes.rbac_subroutes.common import (
    _db,
    _session_service,
    _row_value,
    _tenant_id,
    _actor_user_id,
    _parse_json,
    _iso,
    _http_error,
    _validate_role_transition,
    _stable_display_name,
    _validate_body,
    _department_active,
    _is_customer_service_department,
    _load_cs_agent,
    _cs_role,
    _cs_public,
    _state,
    _merge_agent,
    _rollback,
    _is_concurrency_conflict,
    _department_public,
    _department_audit_ready,
    _write_department_audit,
    _valid_new_username,
    _generate_temp_password,
    _write_user_audit,
    _can_operate_super_admin,
    _audit_item,
)


async def update_user_in_transaction(
    db,
    *,
    user_id: int,
    body: dict[str, Any],
    operator: OperatorIdentity,
    tenant_id: str,
    require_version: bool = True,
) -> UserUpdateOutcome:
    """执行平台用户更新、会话撤销与审计的同事务部分。"""
    parsed = _validate_body(body, require_version=require_version)
    if parsed["cs_fields_present"]:
        raise _http_error("客服档案请在客服团队页面单独管理", 400)
    # 所有 RBAC 写操作先争抢同一事务级 advisory lock，再按固定顺序锁
    # 活动高权限管理员与目标用户，避免两个管理员并发降权时交叉持锁死锁。
    await db.execute(text(
        "SELECT pg_advisory_xact_lock(hashtext("
        "'auth.users:rbac-active-admins'))"))
    admin_rows = (await db.execute(text(
        "SELECT id FROM auth.users "
        "WHERE tenant_id = :tenant_id AND role IN ('admin', 'super_admin') "
        "AND status = 1 "
        "ORDER BY id FOR UPDATE"),
        {"tenant_id": tenant_id})).mappings().all()
    current = (await db.execute(text(
        "SELECT id, username, real_name, email, dept, role, status, version, tenant_id "
        "FROM auth.users "
        "WHERE id = :uid AND tenant_id = :tenant_id FOR UPDATE"),
        {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
    if current is None:
        raise _http_error("用户不存在", 404)

    current_version = int(_row_value(current, "version", 0))
    if require_version and parsed["version"] != current_version:
        raise _http_error("用户版本冲突，请刷新后重试", 409)
    expected_version = current_version if parsed["version"] is None else parsed["version"]

    new_role = (
        _row_value(current, "role", "viewer")
        if parsed["platform_role"] is _MISSING
        else parsed["platform_role"]
    )
    new_status = (
        int(_row_value(current, "status", 1))
        if parsed["status"] is _MISSING
        else parsed["status"]
    )
    _validate_role_transition(
        operator.role,
        _row_value(current, "role", "viewer"),
        new_role,
        new_status,
    )
    # dept（P9）：未携带 = 保持现状；携带空串 = 清空；非空 = 主数据校验
    current_dept = str(_row_value(current, "dept", "") or "")
    new_dept = current_dept if parsed["dept"] is _MISSING else parsed["dept"]
    current_real_name = str(_row_value(current, "real_name", "") or "")
    new_real_name = current_real_name if parsed["real_name"] is _MISSING else parsed["real_name"]
    current_email = str(_row_value(current, "email", "") or "")
    new_email = current_email if parsed["email"] is _MISSING else parsed["email"]
    if parsed["dept"] is not _MISSING and new_dept:
        if not await _department_active(db, tenant_id=tenant_id, code=new_dept):
            raise _http_error(
                f"部门 '{new_dept}' 不存在或未启用（须为本租户 active 部门）", 400)

    if new_dept != current_dept or new_status != 1:
        manager_assignment = (await db.execute(text(
            "SELECT 1 FROM auth.departments "
            "WHERE tenant_id = :tenant_id AND manager_user_id = :user_id "
            "AND status = 1 FOR UPDATE"),
            {"tenant_id": tenant_id, "user_id": user_id})).first()
        if manager_assignment:
            raise _http_error(
                "该用户仍是部门主管，请先在部门管理中更换或取消主管", 409)

    was_active_admin = (
        _row_value(current, "role") in {"admin", "super_admin"}
        and int(_row_value(current, "status", 1)) == 1
    )
    loses_admin_access = new_role not in {"admin", "super_admin"} or new_status != 1
    if was_active_admin and loses_admin_access:
        if len(admin_rows) <= 1:
            raise _http_error("不能降级或禁用最后一个 active admin", 409)

    current_agent = await _load_cs_agent(
        db, tenant_id=tenant_id, user_id=user_id
    )
    before_state = _state(current, current_agent)

    updated = (await db.execute(text(
        "UPDATE auth.users SET role = :role, status = :status, dept = :dept, "
        "real_name = :real_name, email = :email, "
        "version = version + 1, updated_at = now() "
        "WHERE id = :uid AND tenant_id = :tenant_id "
        "AND version = :expected_version "
        "RETURNING id, username, real_name, email, dept, role, status, version, tenant_id"),
        {
            "uid": user_id,
            "tenant_id": tenant_id,
            "role": new_role,
            "status": new_status,
            "dept": new_dept,
            "real_name": new_real_name,
            "email": new_email,
            "expected_version": expected_version,
        })).mappings().first()
    if updated is None:
        raise _http_error("用户版本冲突，请刷新后重试", 409)

    after_agent = current_agent
    if current_agent is not None and new_real_name != current_real_name:
        display_name = _stable_display_name(updated, user_id)
        await db.execute(text(
            "UPDATE customer_service.cs_agents SET display_name = :display_name, "
            "updated_at = now() WHERE tenant_id = :tenant_id "
            "AND agent_id = :agent_id"), {
                "display_name": display_name,
                "tenant_id": tenant_id,
                "agent_id": _row_value(current_agent, "agent_id"),
            })
        after_agent = _merge_agent(after_agent, {"display_name": display_name})

    new_dept_is_customer_service = bool(
        new_dept and await _is_customer_service_department(
            db, tenant_id=tenant_id, code=new_dept
        )
    )
    if current_agent is not None and (
        new_status != 1 or not new_dept_is_customer_service
    ):
        await db.execute(text(
            "UPDATE customer_service.cs_agents SET enabled = FALSE, "
            "accepting = FALSE, updated_at = now() "
            "WHERE tenant_id = :tenant_id AND agent_id = :agent_id"),
            {"tenant_id": tenant_id,
             "agent_id": _row_value(current_agent, "agent_id")})
        after_agent = _merge_agent(
            after_agent, {"enabled": False, "accepting": False}
        )

    after_state = _state(updated, after_agent)
    cs_changes: dict[str, Any] = {}
    if "csRole" in body:
        cs_changes["role"] = parsed["cs_role"]
    for field, parsed_key in (
        ("maxConversations", "max_conversations"),
        ("enabled", "enabled"),
        ("accepting", "accepting"),
    ):
        if field in body:
            cs_changes[field] = parsed[parsed_key]
    if current_agent is not None and after_agent is not current_agent:
        cs_changes.setdefault("enabled", False)
        cs_changes.setdefault("accepting", False)
    after_state["csChanges"] = cs_changes

    actor_user_id = _actor_user_id(operator)
    await db.execute(text(
        "INSERT INTO auth.rbac_audits "
        "(tenant_id, actor_user_id, target_user_id, action, before_state, "
        "after_state, result) VALUES "
        "(:tenant_id, :actor_user_id, :target_user_id, :action, "
        "CAST(:before_state AS JSONB), CAST(:after_state AS JSONB), :result)"),
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "target_user_id": user_id,
            "action": "user.update",
            "before_state": json.dumps(before_state, ensure_ascii=False),
            "after_state": json.dumps(after_state, ensure_ascii=False),
            "result": "success",
        })

    role_changed = new_role != _row_value(current, "role", "viewer")
    disabled = new_status != int(_row_value(current, "status", 1)) and new_status != 1
    dept_changed = new_dept != current_dept
    cs_authorization_changed = any(
        before_state.get(field) != after_state.get(field)
        for field in ("csRole", "enabled")
    )
    revoked_sessions: list[SessionRef] = []
    if role_changed or disabled or dept_changed or cs_authorization_changed:
        # 部门是授权属性（决定 RAG KB 可见范围）：与角色/禁用同语义立即
        # 吊销全部会话（rbac_changed）——平台已有会话闸，改权即失效，
        # 用户重新登录即得最新 claim，不等 access TTL。
        revoked_sessions = await _session_service.revoke_user_sessions(
            db, user_id, reason="rbac_changed"
        )

    public_user = {
        "userId": _row_value(updated, "id", user_id),
        "username": _row_value(updated, "username", _row_value(current, "username")),
        "realName": _row_value(updated, "real_name", _row_value(current, "real_name", "")),
        "email": _row_value(updated, "email", _row_value(current, "email", "")),
        "dept": _row_value(updated, "dept", _row_value(current, "dept", "")),
        "platformRole": _row_value(updated, "role", new_role),
        "status": int(_row_value(updated, "status", new_status)),
        "version": int(_row_value(updated, "version", current_version + 1)),
    }
    return UserUpdateOutcome(user=public_user, revoked_sessions=revoked_sessions)


async def _change_department(
    *, code: str, request: Request, operator: OperatorIdentity,
    version: int, name: str | None = None, status: int | None = None,
    department_type: str | None = None,
    manager_user_id: int | None | object = _MISSING,
) -> dict[str, Any]:
    """按版本修改部门、类型和主管，并审计每次变更。"""
    tenant_id = _tenant_id(request)
    normalized_code = code.strip().lower()
    if not _DEPARTMENT_CODE_RE.fullmatch(normalized_code):
        raise _http_error("部门编码格式无效", 400)
    if version < 0:
        raise _http_error("部门版本号无效", 400)
    if name is not None and (
        not name or len(name) > 100 or any(ord(char) < 32 for char in name)
    ):
        raise _http_error("部门名称不能为空，且不能超过 100 个字符", 400)
    if status is not None and status not in {0, 1}:
        raise _http_error("部门状态只能是启用或停用", 400)
    if department_type is not None and (
        not isinstance(department_type, str)
        or department_type not in {"general", "customer_service"}
    ):
        raise _http_error("departmentType 必须是 general/customer_service", 400)
    if manager_user_id is not _MISSING and manager_user_id is not None and (
        isinstance(manager_user_id, bool)
        or not isinstance(manager_user_id, int)
        or manager_user_id <= 0
    ):
        raise _http_error("managerUserId 必须是正整数或 null", 400)
    if (name is None and status is None and department_type is None
            and manager_user_id is _MISSING):
        raise _http_error("请至少修改部门名称、类型、主管或状态", 400)

    db = None
    try:
        async with _db() as db:
            available = (await db.execute(text(
                "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
                "WHERE table_schema = 'auth' AND table_name = 'departments'"
            ))).scalar()
            if not available:
                raise _http_error(
                    "部门主数据未初始化（请先应用 041_auth_departments 迁移）", 503)
            if not await _department_audit_ready(db):
                raise _http_error(
                    "部门审计表未初始化（请先应用 102_rbac_department_audits 迁移）", 503)

            current = (await db.execute(text(
                "SELECT d.code, d.name, d.status, d.version, d.updated_at, "
                "d.department_type, d.manager_user_id, "
                "m.username AS manager_username, "
                "m.real_name AS manager_real_name "
                "FROM auth.departments d "
                "LEFT JOIN auth.users m ON m.tenant_id = d.tenant_id "
                "AND m.id = d.manager_user_id "
                "WHERE d.tenant_id = :tenant_id AND d.code = :code "
                "FOR UPDATE OF d"
            ), {"tenant_id": tenant_id, "code": normalized_code})).mappings().first()
            if current is None:
                raise _http_error("部门不存在", 404)
            if int(_row_value(current, "version", 0)) != version:
                raise _http_error("部门已被其他管理员修改，请刷新后重试", 409)

            old_state = _department_public(current)
            member_count = int((await db.execute(text(
                "SELECT COUNT(*) FROM auth.users "
                "WHERE tenant_id = :tenant_id AND dept = :code"
            ), {"tenant_id": tenant_id, "code": normalized_code})).scalar() or 0)
            old_state["memberCount"] = member_count
            new_name = name if name is not None else old_state["name"]
            new_status = status if status is not None else old_state["status"]
            new_type = department_type or old_state["departmentType"]
            new_manager_id = (
                old_state["managerUserId"]
                if manager_user_id is _MISSING else manager_user_id
            )
            if old_state["status"] == 1 and new_status == 0:
                if member_count:
                    raise _http_error(
                        f"部门仍有 {member_count} 位用户归属，请先调整用户部门后再停用",
                        409,
                    )

            if new_manager_id is not None:
                manager = (await db.execute(text(
                    "SELECT id FROM auth.users WHERE id = :user_id "
                    "AND tenant_id = :tenant_id AND dept = :code AND status = 1 "
                    "FOR UPDATE"), {
                        "user_id": new_manager_id,
                        "tenant_id": tenant_id,
                        "code": normalized_code,
                    })).first()
                if manager is None:
                    raise _http_error("主管必须是本租户该部门的启用用户", 400)

            if new_status == 1 and new_type == "customer_service":
                existing_cs_department = (await db.execute(text(
                    "SELECT 1 FROM auth.departments "
                    "WHERE tenant_id = :tenant_id AND code <> :code "
                    "AND status = 1 AND department_type = 'customer_service' "
                    "LIMIT 1"), {
                        "tenant_id": tenant_id, "code": normalized_code,
                    })).first()
                if existing_cs_department:
                    raise _http_error("本租户已有启用的客服部门", 409)

            if (old_state["departmentType"] == "customer_service"
                    and new_type != "customer_service"):
                enabled_agent = (await db.execute(text(
                    "SELECT 1 FROM customer_service.cs_agents a "
                    "JOIN auth.users u ON a.auth_user_id = CAST(u.id AS VARCHAR) "
                    "AND u.tenant_id = a.tenant_id "
                    "WHERE u.tenant_id = :tenant_id AND u.dept = :code "
                    "AND a.enabled IS TRUE LIMIT 1"), {
                        "tenant_id": tenant_id, "code": normalized_code,
                    })).first()
                if enabled_agent:
                    raise _http_error(
                        "客服部门仍有启用的客服档案，请先在客服团队中停用或移出成员",
                        409,
                    )

            if (
                new_name == old_state["name"]
                and new_status == old_state["status"]
                and new_type == old_state["departmentType"]
                and new_manager_id == old_state["managerUserId"]
            ):
                await db.commit()
                return old_state

            updated = (await db.execute(text(
                "UPDATE auth.departments SET name = :name, status = :status, "
                "department_type = :department_type, "
                "manager_user_id = :manager_user_id, "
                "version = version + 1, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND code = :code "
                "AND version = :version "
                "RETURNING code, name, status, version, updated_at, "
                "department_type, manager_user_id"
            ), {
                "tenant_id": tenant_id, "code": normalized_code,
                "name": new_name, "status": new_status,
                "department_type": new_type,
                "manager_user_id": new_manager_id,
                "version": version,
            })).mappings().first()
            if updated is None:
                raise _http_error("部门已被其他管理员修改，请刷新后重试", 409)

            updated_public = (await db.execute(text(
                "SELECT d.code, d.name, d.status, d.version, d.updated_at, "
                "d.department_type, d.manager_user_id, "
                "m.username AS manager_username, "
                "m.real_name AS manager_real_name "
                "FROM auth.departments d "
                "LEFT JOIN auth.users m ON m.tenant_id = d.tenant_id "
                "AND m.id = d.manager_user_id "
                "WHERE d.tenant_id = :tenant_id AND d.code = :code"), {
                    "tenant_id": tenant_id, "code": normalized_code,
                })).mappings().first()
            new_state = _department_public(updated_public or updated)
            new_state["memberCount"] = member_count
            name_changed = new_name != old_state["name"]
            status_changed = new_status != old_state["status"]
            manager_changed = new_manager_id != old_state["managerUserId"]
            action = "department.update"
            if manager_changed and not (
                name_changed or status_changed
                or new_type != old_state["departmentType"]
            ):
                action = "department.manager_changed"
            elif not name_changed and new_type == old_state["departmentType"]:
                action = "department.activate" if new_status == 1 else "department.deactivate"
            await _write_department_audit(
                db, tenant_id=tenant_id, operator=operator,
                code=normalized_code, name=new_name, action=action,
                before_state=old_state, after_state=new_state,
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
                detail="部门发生并发冲突，请刷新后重试",
            ) from exc
        logger.exception("[RBAC] 部门更新事务失败 tenant_id=%s code=%s", tenant_id, normalized_code)
        raise HTTPException(status_code=500, detail="部门更新失败") from exc

    updated_state = _department_public(updated_public or updated)
    updated_state["memberCount"] = member_count
    return updated_state


async def _update_department_member_in_transaction(
    db,
    *,
    user_id: int,
    body: Any,
    access: RbacAccess,
    tenant_id: str,
) -> UserUpdateOutcome:
    """部门主管仅修改本部门成员资料或账号状态。"""
    allowed_fields = {"version", "realName", "email", "status"}
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)
    if set(body) - allowed_fields:
        raise _http_error("部门主管只能修改成员资料和账号状态", 403)
    parsed = _validate_body(body, require_version=True)
    actor_user_id = _actor_user_id(access.operator)
    if actor_user_id is None:
        raise _http_error("缺少有效的部门主管身份", 403)

    current = (await db.execute(text(
        "SELECT id, username, real_name, email, dept, role, status, version "
        "FROM auth.users WHERE id = :user_id AND tenant_id = :tenant_id "
        "FOR UPDATE"), {
            "user_id": user_id, "tenant_id": tenant_id,
        })).mappings().first()
    if current is None:
        raise _http_error("用户不存在", 404)
    current_dept = str(_row_value(current, "dept", "") or "")
    if current_dept not in access.managed_departments:
        raise _http_error("不能管理授权部门范围外的用户", 404)
    assignment = (await db.execute(text(
        "SELECT 1 FROM auth.departments WHERE tenant_id = :tenant_id "
        "AND code = :dept AND manager_user_id = :actor_user_id "
        "AND status = 1 FOR UPDATE"), {
            "tenant_id": tenant_id,
            "dept": current_dept,
            "actor_user_id": actor_user_id,
        })).first()
    if assignment is None:
        raise _http_error("部门主管授权已变更，请刷新后重试", 403)

    current_version = int(_row_value(current, "version", 0))
    if parsed["version"] != current_version:
        raise _http_error("用户版本冲突，请刷新后重试", 409)
    current_status = int(_row_value(current, "status", 1))
    new_status = current_status if parsed["status"] is _MISSING else parsed["status"]
    if user_id == actor_user_id and new_status != current_status:
        raise _http_error("部门主管不能通过此页面停用自己的账号", 403)
    if current_status == 1 and new_status == 0:
        manages_department = (await db.execute(text(
            "SELECT 1 FROM auth.departments WHERE tenant_id = :tenant_id "
            "AND manager_user_id = :user_id AND status = 1 "
            "LIMIT 1 FOR UPDATE"), {
                "tenant_id": tenant_id,
                "user_id": user_id,
            })).first()
        if manages_department:
            raise _http_error(
                "该用户仍担任部门主管，请先更换或取消主管配置", 409
            )
    if (
        new_status != current_status
        and _row_value(current, "role") in {"admin", "super_admin"}
    ):
        raise _http_error("部门主管不能变更平台管理员账号状态", 403)

    current_name = str(_row_value(current, "real_name", "") or "")
    current_email = str(_row_value(current, "email", "") or "")
    new_name = current_name if parsed["real_name"] is _MISSING else parsed["real_name"]
    new_email = current_email if parsed["email"] is _MISSING else parsed["email"]
    if (new_status == current_status and new_name == current_name
            and new_email == current_email):
        return UserUpdateOutcome(
            user={
                "userId": user_id,
                "username": _row_value(current, "username"),
                "realName": current_name,
                "email": current_email,
                "dept": current_dept,
                "status": current_status,
                "version": current_version,
            },
            revoked_sessions=[],
        )

    updated = (await db.execute(text(
        "UPDATE auth.users SET real_name = :real_name, email = :email, "
        "status = :status, version = version + 1, updated_at = now() "
        "WHERE id = :user_id AND tenant_id = :tenant_id "
        "AND version = :version "
        "RETURNING id, username, real_name, email, dept, status, version"), {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "real_name": new_name,
            "email": new_email,
            "status": new_status,
            "version": current_version,
        })).mappings().first()
    if updated is None:
        raise _http_error("用户版本冲突，请刷新后重试", 409)

    await db.execute(text(
        "UPDATE customer_service.cs_agents SET display_name = :display_name, "
        "updated_at = now() WHERE tenant_id = :tenant_id "
        "AND auth_user_id = CAST(:user_id AS VARCHAR)"), {
            "display_name": new_name or _row_value(current, "username"),
            "tenant_id": tenant_id,
            "user_id": user_id,
        })

    before_state = {
        "realName": current_name,
        "email": current_email,
        "dept": current_dept,
        "status": current_status,
    }
    after_state = {
        "realName": new_name,
        "email": new_email,
        "dept": current_dept,
        "status": new_status,
    }
    if current_status == 1 and new_status == 0:
        disabled_agent = (await db.execute(text(
            "UPDATE customer_service.cs_agents SET enabled = FALSE, "
            "accepting = FALSE, updated_at = now() "
            "WHERE tenant_id = :tenant_id "
            "AND auth_user_id = CAST(:user_id AS VARCHAR) "
            "RETURNING agent_id"), {
                "tenant_id": tenant_id,
                "user_id": user_id,
            })).first()
        if disabled_agent:
            after_state["csChanges"] = {"enabled": False, "accepting": False}
    await db.execute(text(
        "INSERT INTO auth.rbac_audits "
        "(tenant_id, actor_user_id, target_user_id, action, before_state, "
        "after_state, result) VALUES (:tenant_id, :actor_user_id, :target_user_id, "
        "'department_member.update', CAST(:before_state AS JSONB), "
        "CAST(:after_state AS JSONB), 'success')"), {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "target_user_id": user_id,
            "before_state": json.dumps(before_state, ensure_ascii=False),
            "after_state": json.dumps(after_state, ensure_ascii=False),
        })

    revoked_sessions = []
    if current_status == 1 and new_status == 0:
        revoked_sessions = await _session_service.revoke_user_sessions(
            db, user_id, reason="rbac_changed"
        )
    return UserUpdateOutcome(
        user={
            "userId": user_id,
            "username": _row_value(updated, "username"),
            "realName": _row_value(updated, "real_name") or "",
            "email": _row_value(updated, "email") or "",
            "dept": _row_value(updated, "dept") or "",
            "status": int(_row_value(updated, "status", 1)),
            "version": int(_row_value(updated, "version", current_version + 1)),
        },
        revoked_sessions=revoked_sessions,
    )

