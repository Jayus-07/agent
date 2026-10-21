"""管理端 RBAC、客服档案与会话撤销 API。"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.app.api.deps import (
    OperatorIdentity,
    is_platform_admin,
    require_admin_user,
    require_user_actor,
)
from backend.app.api.identity import resolve_identity
from backend.infra.redis.client import get_auth_redis
from backend.memory.database import get_session
from backend.security.local_jwt import hash_password
from backend.security.session_service import SessionRef, SessionService
from backend.shared.logger import logger

router = APIRouter(prefix="/sys/rbac", tags=["管理端-RBAC"])

_ALLOWED_PLATFORM_ROLES = {"viewer", "editor", "admin", "super_admin"}
_ALLOWED_CS_ROLES = {"agent", "supervisor"}
_DEPARTMENT_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")
_MISSING = object()
_session_service = SessionService(redis_getter=get_auth_redis)


@asynccontextmanager
async def _db():
    """以显式 commit 方式使用项目异步数据库生成器。"""
    async for session in get_session():
        yield session
        break


async def require_rbac_admin(
    operator: OperatorIdentity = Depends(require_admin_user),
) -> OperatorIdentity:
    """RBAC 管理面强制 admin，避免敏感端点 audit 灰度放行非管理员。"""
    if not is_platform_admin(operator.role):
        # M9：RBAC 硬拒旁路落 security_events（audit 灰度放行后的兜底拒绝）
        try:
            from backend.security.events import record_security_event

            record_security_event(
                "AUTHZ_DENIED", category="rbac_admin",
                detail={"actor": operator.actor, "role": operator.role},
            )
        except Exception:
            pass
        raise HTTPException(status_code=403, detail="仅 admin 可访问 RBAC 管理端")
    return operator


@dataclass(frozen=True)
class RbacAccess:
    """平台管理员或当前操作者可管理的部门范围。"""

    operator: OperatorIdentity
    is_platform_admin: bool
    managed_departments: tuple[str, ...] = ()


async def _manager_departments(db, *, tenant_id: str, user_id: int) -> list[dict[str, str]]:
    rows = (await db.execute(text(
        "SELECT d.code, d.name FROM auth.departments d "
        "JOIN auth.users u ON u.id = d.manager_user_id "
        "AND u.tenant_id = d.tenant_id AND u.dept = d.code AND u.status = 1 "
        "WHERE d.tenant_id = :tenant_id AND d.status = 1 "
        "AND d.manager_user_id = :user_id ORDER BY d.name, d.code"),
        {"tenant_id": tenant_id, "user_id": user_id})).mappings().all()
    return [{"code": row["code"], "name": row["name"]} for row in rows]


async def require_rbac_access(
    request: Request,
    operator: OperatorIdentity = Depends(require_user_actor),
) -> RbacAccess:
    """允许平台管理员或有有效部门主管关联的用户访问用户管理读写接口。"""
    if is_platform_admin(operator.role):
        return RbacAccess(operator=operator, is_platform_admin=True)

    actor_user_id = _actor_user_id(operator)
    if actor_user_id is None:
        raise _http_error("当前账号没有访问控制权限", 403)
    tenant_id = _tenant_id(request)
    async with _db() as db:
        departments = await _manager_departments(
            db, tenant_id=tenant_id, user_id=actor_user_id
        )
    if not departments:
        raise _http_error("当前账号没有部门主管授权", 403)
    return RbacAccess(
        operator=operator,
        is_platform_admin=False,
        managed_departments=tuple(item["code"] for item in departments),
    )


@dataclass(frozen=True)
class UserUpdateOutcome:
    """一次已写入当前事务、等待提交的用户更新结果。"""

    user: dict[str, Any]
    revoked_sessions: list[SessionRef]


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _tenant_id(request: Request) -> str:
    """只消费统一身份入口提供的租户，缺失时拒绝而不是猜 default。"""
    identity = resolve_identity(request)
    if not identity.authenticated or not identity.tenant_id:
        raise _http_error("缺少可信租户身份", 403)
    return identity.tenant_id


def _actor_user_id(operator: OperatorIdentity) -> int | None:
    actor = operator.actor or ""
    if not actor.startswith("user:"):
        return None
    raw = actor[5:]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _http_error(message: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


def _validate_role_transition(
    operator_role: str,
    current_role: str | None,
    new_role: str,
    new_status: int,
    *,
    creating: bool = False,
) -> None:
    """校验普通管理 HTTP API 的平台角色与状态变更边界。"""
    del creating
    if not is_platform_admin(operator_role):
        raise _http_error("仅管理员可管理 RBAC", 403)
    if current_role == "super_admin" or new_role == "super_admin":
        raise _http_error("super_admin 只能由运维 bootstrap 管理", 403)
    if operator_role != "super_admin" and (
        current_role == "admin" or new_role == "admin"
    ):
        raise _http_error("仅 super_admin 可管理 admin", 403)
    if new_status not in (0, 1):
        raise _http_error("status 必须是 0/1", 400)


def _stable_display_name(user: Any, user_id: int) -> str:
    """客服档案显示名只取服务端用户资料，并提供稳定的最终回退。"""
    for key in ("real_name", "username"):
        value = _row_value(user, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"user-{user_id}"


def _validate_body(body: Any, *, require_version: bool) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise _http_error("请求体必须是 JSON 对象", 400)

    version = body.get("version", _MISSING)
    if require_version and version is _MISSING:
        raise _http_error("必须携带 version", 400)
    if version is not _MISSING and (
        isinstance(version, bool) or not isinstance(version, int) or version < 0
    ):
        raise _http_error("version 必须是非负整数", 400)

    platform_role = body.get("platformRole", _MISSING)
    # 旧 /sys/users/{id}/role 兼容入口由调用方显式传入 role。
    if platform_role is _MISSING and "role" in body:
        platform_role = body["role"]
    if platform_role is not _MISSING and (
        not isinstance(platform_role, str)
        or platform_role not in _ALLOWED_PLATFORM_ROLES
    ):
        raise _http_error("platformRole 必须是 viewer/editor/admin/super_admin", 400)

    status = body.get("status", _MISSING)
    if status is not _MISSING:
        if isinstance(status, bool):
            status = int(status)
        elif isinstance(status, str) and status.strip().lower() in {
            "active", "enabled", "1",
        }:
            status = 1
        elif isinstance(status, str) and status.strip().lower() in {
            "disabled", "inactive", "0",
        }:
            status = 0
        if status not in (0, 1):
            raise _http_error("status 必须是 0/1 或 active/disabled", 400)

    for field in ("enabled", "accepting"):
        if field in body and not isinstance(body[field], bool):
            raise _http_error(f"{field} 必须是布尔值", 400)

    max_conversations = body.get("maxConversations", _MISSING)
    if max_conversations is not _MISSING and (
        isinstance(max_conversations, bool)
        or not isinstance(max_conversations, int)
        or max_conversations <= 0
    ):
        raise _http_error("maxConversations 必须是正整数", 400)

    cs_role = body.get("csRole", _MISSING)
    if cs_role is not _MISSING and cs_role is not None and (
        not isinstance(cs_role, str) or cs_role not in _ALLOWED_CS_ROLES
    ):
        raise _http_error("csRole 必须是 agent/supervisor/null", 400)

    # dept（P9 部门维护）：空串/null = 清空部门；非空必须是字符串且在
    # 事务内校验为本租户 active 部门（_department_active），不收任意文本。
    dept = body.get("dept", _MISSING)
    if dept is not _MISSING:
        if dept is None:
            dept = ""
        elif not isinstance(dept, str):
            raise _http_error("dept 必须是字符串或 null", 400)
        else:
            dept = dept.strip()[:50]

    real_name = body.get("realName", _MISSING)
    if real_name is not _MISSING:
        if not isinstance(real_name, str):
            raise _http_error("realName 必须是字符串", 400)
        real_name = real_name.strip()
        if len(real_name) > 100 or any(ord(char) < 32 for char in real_name):
            raise _http_error("姓名不能超过 100 个字符", 400)

    email = body.get("email", _MISSING)
    if email is not _MISSING:
        if not isinstance(email, str):
            raise _http_error("email 必须是字符串", 400)
        email = email.strip()
        if len(email) > 255 or any(ord(char) < 32 for char in email):
            raise _http_error("邮箱格式无效", 400)

    return {
        "version": None if version is _MISSING else version,
        "platform_role": platform_role,
        "status": status,
        "cs_role": cs_role,
        "max_conversations": max_conversations,
        "enabled": body.get("enabled", _MISSING),
        "accepting": body.get("accepting", _MISSING),
        "dept": dept,
        "real_name": real_name,
        "email": email,
        "cs_fields_present": any(
            key in body
            for key in ("csRole", "maxConversations", "enabled", "accepting")
        ),
    }


async def _department_active(db, *, tenant_id: str, code: str) -> bool:
    """部门主数据校验（P3）：本租户 + code 命中 + active。

    fail-closed：auth.departments 未就绪（041 迁移未应用）时显式报错，
    不降级放行——部门校验是授权属性，静默放行等于绕过主数据约束。
    """
    available = (await db.execute(text(
        "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
        "WHERE table_schema = 'auth' AND table_name = 'departments'"))).scalar()
    if not available:
        raise _http_error("部门主数据未初始化（请先应用 041_auth_departments 迁移）", 503)
    row = (await db.execute(text(
        "SELECT 1 FROM auth.departments "
        "WHERE tenant_id = :tenant_id AND code = :code AND status = 1 "
        "FOR UPDATE"),
        {"tenant_id": tenant_id, "code": code})).first()
    return row is not None


async def _is_customer_service_department(
    db, *, tenant_id: str, code: str
) -> bool:
    row = (await db.execute(text(
        "SELECT 1 FROM auth.departments "
        "WHERE tenant_id = :tenant_id AND code = :code "
        "AND department_type = 'customer_service'"),
        {"tenant_id": tenant_id, "code": code})).first()
    return row is not None


async def _load_cs_agent(db, *, tenant_id: str, user_id: int):
    row = (await db.execute(text(
        "SELECT agent_id, tenant_id, auth_user_id, display_name, role, "
        "max_conversations, "
        "enabled, accepting FROM customer_service.cs_agents "
        "WHERE tenant_id = :tenant_id AND auth_user_id = CAST(:uid AS VARCHAR) "
        "FOR UPDATE"),
        {"tenant_id": tenant_id, "uid": str(user_id)})).mappings().first()
    # 兼容只返回部分列的测试/旧边界替身；没有 agent_id 就不是客服档案。
    return row if _row_value(row, "agent_id") else None


def _cs_role(agent: Any) -> str | None:
    if agent is None or not _row_value(agent, "enabled", False):
        return None
    role = _row_value(agent, "role")
    return role if role in _ALLOWED_CS_ROLES else None


def _cs_public(agent: Any) -> dict[str, Any] | None:
    if agent is None:
        return None
    return {
        "agentId": _row_value(agent, "agent_id"),
        "displayName": _row_value(agent, "display_name"),
        "role": _row_value(agent, "role"),
        "maxConversations": _row_value(agent, "max_conversations"),
        "enabled": _row_value(agent, "enabled"),
        "accepting": _row_value(agent, "accepting"),
    }


def _state(user: Any, agent: Any) -> dict[str, Any]:
    return {
        "realName": _row_value(user, "real_name", ""),
        "email": _row_value(user, "email", ""),
        "platformRole": _row_value(user, "role", "viewer"),
        "status": int(_row_value(user, "status", 1)),
        "dept": _row_value(user, "dept", ""),
        "csRole": _cs_role(agent),
        "displayName": _row_value(agent, "display_name") if agent else None,
        "maxConversations": _row_value(agent, "max_conversations") if agent else None,
        "enabled": _row_value(agent, "enabled") if agent else None,
        "accepting": _row_value(agent, "accepting") if agent else None,
    }


def _merge_agent(agent: Any, values: dict[str, Any]) -> dict[str, Any]:
    merged = dict(agent or {})
    merged.update(values)
    return merged


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


async def _rollback(db) -> None:
    if db is None or not hasattr(db, "rollback"):
        return
    try:
        await db.rollback()
    except Exception:
        logger.warning("[RBAC] 事务回滚失败", exc_info=True)


def _is_concurrency_conflict(exc: BaseException) -> bool:
    """识别 PostgreSQL 并发/唯一性冲突，统一映射为可重试的 409。"""
    candidates = [exc, getattr(exc, "orig", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code in {"40001", "40P01", "55P03", "23505"}:
            return True
    return False


def _department_public(row: Any) -> dict[str, Any]:
    updated_at = _row_value(row, "updated_at")
    return {
        "code": _row_value(row, "code"),
        "name": _row_value(row, "name"),
        "status": int(_row_value(row, "status", 1)),
        "version": int(_row_value(row, "version", 0)),
        "departmentType": _row_value(row, "department_type", "general"),
        "managerUserId": _row_value(row, "manager_user_id"),
        "managerUsername": _row_value(row, "manager_username"),
        "managerName": _row_value(row, "manager_real_name")
        or _row_value(row, "manager_username"),
        "memberCount": int(_row_value(row, "member_count", 0) or 0),
        "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
    }


async def _department_audit_ready(db) -> bool:
    return bool((await db.execute(text(
        "SELECT COUNT(*) = 1 AS available FROM information_schema.tables "
        "WHERE table_schema = 'auth' AND table_name = 'department_audits'"
    ))).scalar())


async def _write_department_audit(
    db, *, tenant_id: str, operator: OperatorIdentity, code: str,
    name: str, action: str, before_state: dict[str, Any],
    after_state: dict[str, Any],
) -> None:
    await db.execute(text(
        "INSERT INTO auth.department_audits "
        "(tenant_id, actor_user_id, department_code, department_name, "
        "action, before_state, after_state, result) VALUES "
        "(:tenant_id, :actor_user_id, :code, :name, :action, "
        "CAST(:before_state AS JSONB), CAST(:after_state AS JSONB), 'success')"
    ), {
        "tenant_id": tenant_id,
        "actor_user_id": _actor_user_id(operator),
        "code": code,
        "name": name,
        "action": action,
        "before_state": json.dumps(before_state, ensure_ascii=False),
        "after_state": json.dumps(after_state, ensure_ascii=False),
    })


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
    return await _change_department(
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
    return await _change_department(
        code=code, request=request, operator=operator, version=version,
        status=0,
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
                outcome = await update_user_in_transaction(
                    db, user_id=user_id, body=body,
                    operator=access.operator, tenant_id=tenant_id,
                )
            else:
                outcome = await _update_department_member_in_transaction(
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


# ── P6 用户生命周期（2026-09-21，治理实施方案 §P6）──────────────

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,20}$")


def _generate_temp_password() -> str:
    """随机临时密码（大小写 + 数字，16 位，无歧义字符集）。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


async def _write_user_audit(db, *, tenant_id: str, actor_user_id: int | None,
                            target_user_id: int, action: str,
                            after_state: dict) -> None:
    """用户生命周期动作审计（user.create / reset_password / force_logout）。

    审计失败不阻断主流程（记录日志后继续）。
    """
    try:
        await db.execute(text(
            "INSERT INTO auth.rbac_audits "
            "(tenant_id, actor_user_id, target_user_id, action, "
            "before_state, after_state, result) VALUES "
            "(:tenant_id, :actor_user_id, :target_user_id, :action, "
            "CAST(:before_state AS JSONB), CAST(:after_state AS JSONB), 'success')"),
            {
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "target_user_id": target_user_id,
                "action": action,
                "before_state": json.dumps({}),
                "after_state": json.dumps(after_state, ensure_ascii=False),
            })
    except Exception:  # noqa: BLE001 — 审计失败不阻断主流程（记录日志）
        logger.warning("[RBAC] 用户生命周期审计写入失败 action=%s", action, exc_info=True)


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
    if not _USERNAME_RE.match(username):
        raise _http_error("username 必须为 2-20 位字母/数字/_.-", 400)
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
            "SELECT id, username FROM auth.users "
            "WHERE id = :uid AND tenant_id = :tenant_id FOR UPDATE"),
            {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
        if row is None:
            raise _http_error("用户不存在", 404)
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
            "SELECT id, username FROM auth.users "
            "WHERE id = :uid AND tenant_id = :tenant_id"),
            {"uid": user_id, "tenant_id": tenant_id})).mappings().first()
        if row is None:
            raise _http_error("用户不存在", 404)
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


def _audit_item(row: Any) -> dict[str, Any]:
    before = _parse_json(_row_value(row, "before_state", {}))
    after = _parse_json(_row_value(row, "after_state", {}))
    actor_user_id = _row_value(row, "actor_user_id")
    target_user_id = _row_value(row, "target_user_id")
    actor_username = _row_value(row, "actor_username")
    target_username = _row_value(row, "target_username")
    operator = f"user:{actor_user_id}" if actor_user_id is not None else "service"
    return {
        "id": _row_value(row, "id"),
        "operator": operator,
        "actor": operator,
        "actorUserId": actor_user_id,
        "actorUsername": actor_username,
        "target": f"user:{target_user_id}",
        "targetUserId": target_user_id,
        "targetUsername": target_username,
        "action": _row_value(row, "action"),
        "oldPlatformRole": before.get("platformRole"),
        "newPlatformRole": after.get("platformRole"),
        "csChanges": after.get("csChanges", {}),
        "beforeState": before,
        "afterState": after,
        "result": _row_value(row, "result"),
        "createdAt": _iso(_row_value(row, "created_at")),
    }


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


__all__ = ["router", "require_rbac_admin", "update_user_in_transaction"]
