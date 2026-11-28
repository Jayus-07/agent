"""RBAC 通用工具函数、权限校验与审计辅助。"""
from __future__ import annotations

import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, HTTPException, Query, Request
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
from backend.app.api.routes.rbac_subroutes.dynamic import (
    DynamicDbProxy,
    DynamicSessionServiceProxy,
    current_func,
)

_session_service_instance = SessionService(redis_getter=get_auth_redis)
_session_service = DynamicSessionServiceProxy(_session_service_instance)


@asynccontextmanager
async def _db_raw():
    """以显式 commit 方式使用项目异步数据库生成器。"""
    async for session in get_session():
        yield session
        break



_db = DynamicDbProxy(_db_raw)


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


def _valid_new_username(username: str) -> bool:
    """新账号标识：11 位数字，或 ops_/svc_ 前缀的运维/系统账号。"""
    if len(username) == 11 and username.isdigit():
        return True
    return username.startswith(_OPS_USERNAME_PREFIXES) and bool(
        _USERNAME_RE.match(username)
    )


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


def _can_operate_super_admin(operator_role: str, target_role: str) -> bool:
    """super_admin 账号只允许 super_admin 操作（2026-11-09 审计）。"""
    target = str(target_role or "").lower()
    operator = str(operator_role or "").lower()
    return target != "super_admin" or operator == "super_admin"


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

