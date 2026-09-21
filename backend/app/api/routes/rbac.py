"""管理端 RBAC、客服档案与会话撤销 API。"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.app.api.identity import resolve_identity
from backend.memory.database import get_session
from backend.security.session_service import SessionRef, SessionService
from backend.shared.logger import logger

router = APIRouter(prefix="/sys/rbac", tags=["管理端-RBAC"])

_ALLOWED_PLATFORM_ROLES = {"viewer", "editor", "admin"}
_ALLOWED_CS_ROLES = {"agent", "supervisor"}
_MISSING = object()
_session_service = SessionService()


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
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail="仅 admin 可访问 RBAC 管理端")
    return operator


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
        raise _http_error("platformRole 必须是 viewer/editor/admin", 400)

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

    return {
        "version": None if version is _MISSING else version,
        "platform_role": platform_role,
        "status": status,
        "cs_role": cs_role,
        "max_conversations": max_conversations,
        "enabled": body.get("enabled", _MISSING),
        "accepting": body.get("accepting", _MISSING),
        "cs_fields_present": any(
            key in body
            for key in ("csRole", "maxConversations", "enabled", "accepting")
        ),
    }


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
        "platformRole": _row_value(user, "role", "viewer"),
        "status": int(_row_value(user, "status", 1)),
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
    """执行用户、客服档案、会话撤销与审计的同事务部分。"""
    parsed = _validate_body(body, require_version=require_version)
    # 所有 RBAC 写操作先争抢同一事务级 advisory lock，再按固定顺序锁
    # active admin 与目标用户，避免两个管理员并发降权时交叉持锁死锁。
    await db.execute(text(
        "SELECT pg_advisory_xact_lock(hashtext("
        "'auth.users:rbac-active-admins'))"))
    admin_rows = (await db.execute(text(
        "SELECT id FROM auth.users "
        "WHERE tenant_id = :tenant_id AND role = 'admin' AND status = 1 "
        "ORDER BY id FOR UPDATE"),
        {"tenant_id": tenant_id})).mappings().all()
    current = (await db.execute(text(
        "SELECT id, username, real_name, dept, role, status, version, tenant_id "
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

    was_active_admin = (
        _row_value(current, "role") == "admin"
        and int(_row_value(current, "status", 1)) == 1
    )
    loses_admin_access = new_role != "admin" or new_status != 1
    if was_active_admin and loses_admin_access:
        if len(admin_rows) <= 1:
            raise _http_error("不能降级或禁用最后一个 active admin", 409)

    current_agent = await _load_cs_agent(
        db, tenant_id=tenant_id, user_id=user_id
    )
    before_state = _state(current, current_agent)

    updated = (await db.execute(text(
        "UPDATE auth.users SET role = :role, status = :status, "
        "version = version + 1, updated_at = now() "
        "WHERE id = :uid AND tenant_id = :tenant_id "
        "AND version = :expected_version "
        "RETURNING id, username, real_name, dept, role, status, version, tenant_id"),
        {
            "uid": user_id,
            "tenant_id": tenant_id,
            "role": new_role,
            "status": new_status,
            "expected_version": expected_version,
        })).mappings().first()
    if updated is None:
        raise _http_error("用户版本冲突，请刷新后重试", 409)

    after_agent = current_agent
    if parsed["cs_fields_present"]:
        requested_role = parsed["cs_role"]
        if requested_role is None and "csRole" in body:
            if current_agent is not None:
                await db.execute(text(
                    "UPDATE customer_service.cs_agents SET enabled = FALSE, "
                    "accepting = FALSE, updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND agent_id = :agent_id"),
                    {"tenant_id": tenant_id,
                     "agent_id": _row_value(current_agent, "agent_id")})
                after_agent = _merge_agent(
                    current_agent, {"enabled": False, "accepting": False}
                )
        elif current_agent is not None:
            values = {
                "role": (
                    _row_value(current_agent, "role", "agent")
                    if requested_role is _MISSING
                    else requested_role
                ),
                "max_conversations": (
                    _row_value(current_agent, "max_conversations", 10)
                    if parsed["max_conversations"] is _MISSING
                    else parsed["max_conversations"]
                ),
                "enabled": (
                    _row_value(current_agent, "enabled", True)
                    if parsed["enabled"] is _MISSING
                    else parsed["enabled"]
                ),
                "accepting": (
                    _row_value(current_agent, "accepting", True)
                    if parsed["accepting"] is _MISSING
                    else parsed["accepting"]
                ),
            }
            await db.execute(text(
                "UPDATE customer_service.cs_agents SET role = :role, "
                "max_conversations = :max_conversations, enabled = :enabled, "
                "accepting = :accepting, updated_at = now() "
                "WHERE tenant_id = :tenant_id AND agent_id = :agent_id"),
                {**values, "tenant_id": tenant_id,
                 "agent_id": _row_value(current_agent, "agent_id")})
            after_agent = _merge_agent(current_agent, values)
        elif requested_role is not None or requested_role is _MISSING:
            # agent_id 由服务端生成，永远不读取浏览器传入的同名字段。
            values = {
                "agent_id": f"cs-{uuid.uuid4().hex}",
                "tenant_id": tenant_id,
                "auth_user_id": str(user_id),
                "display_name": _stable_display_name(current, user_id),
                "role": "agent" if requested_role is _MISSING else requested_role,
                "max_conversations": (
                    10 if parsed["max_conversations"] is _MISSING
                    else parsed["max_conversations"]
                ),
                "enabled": (
                    True if parsed["enabled"] is _MISSING else parsed["enabled"]
                ),
                "accepting": (
                    True if parsed["accepting"] is _MISSING else parsed["accepting"]
                ),
            }
            await db.execute(text(
                "INSERT INTO customer_service.cs_agents "
                "(agent_id, tenant_id, auth_user_id, display_name, role, "
                "max_conversations, "
                "enabled, accepting) VALUES "
                "(:agent_id, :tenant_id, :auth_user_id, :display_name, :role, "
                ":max_conversations, :enabled, :accepting)"), values)
            after_agent = values

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
    cs_authorization_changed = any(
        before_state.get(field) != after_state.get(field)
        for field in ("csRole", "enabled", "accepting")
    )
    revoked_sessions: list[SessionRef] = []
    if role_changed or disabled or cs_authorization_changed:
        revoked_sessions = await _session_service.revoke_user_sessions(
            db, user_id, reason="rbac_changed"
        )

    public_user = {
        "userId": _row_value(updated, "id", user_id),
        "username": _row_value(updated, "username", _row_value(current, "username")),
        "realName": _row_value(updated, "real_name", _row_value(current, "real_name", "")),
        "dept": _row_value(updated, "dept", _row_value(current, "dept", "")),
        "platformRole": _row_value(updated, "role", new_role),
        "status": int(_row_value(updated, "status", new_status)),
        "version": int(_row_value(updated, "version", current_version + 1)),
        "csAgent": _cs_public(after_agent),
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


@router.get("/users")
async def list_users(
    request: Request,
    search: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """分页列出用户和客服档案摘要。"""
    del operator
    search = search.strip()
    tenant_id = _tenant_id(request)
    where = " WHERE u.tenant_id = :tenant_id"
    params: dict[str, Any] = {
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "tenant_id": tenant_id,
    }
    if search:
        where += (
            " AND (u.username ILIKE :search OR u.real_name ILIKE :search "
            "OR u.dept ILIKE :search)"
        )
        params["search"] = f"%{search}%"

    async with _db() as db:
        total_result = await db.execute(text(
            f"SELECT COUNT(*) FROM auth.users u{where}"), params)
        total = int(total_result.scalar() or 0)
        rows = (await db.execute(text(
            "SELECT u.id AS user_id, u.username, u.real_name, u.dept, "
             "u.role AS platform_role, u.status, u.version, u.tenant_id, "
            "COUNT(DISTINCT s.id) AS session_count, "
             "a.agent_id AS cs_agent_id, a.display_name AS cs_display_name, "
             "a.role AS cs_role, "
            "a.max_conversations AS cs_max_conversations, "
            "a.enabled AS cs_enabled, a.accepting AS cs_accepting "
            "FROM auth.users u "
            "LEFT JOIN auth.sessions s ON s.user_id = u.id "
            "AND s.revoked_at IS NULL AND s.refresh_expires_at > now() "
            "LEFT JOIN customer_service.cs_agents a "
            "ON a.auth_user_id = CAST(u.id AS VARCHAR) "
            "AND a.tenant_id = :tenant_id "
            f"{where} "
            "GROUP BY u.id, u.username, u.real_name, u.dept, u.role, u.status, "
             "u.version, u.tenant_id, a.agent_id, a.display_name, a.role, "
             "a.max_conversations, a.enabled, "
            "a.accepting ORDER BY u.id LIMIT :limit OFFSET :offset"),
            params)).mappings().all()

    items = []
    for row in rows:
        agent = None
        if _row_value(row, "cs_agent_id"):
            agent = {
                "agent_id": _row_value(row, "cs_agent_id"),
                "display_name": _row_value(row, "cs_display_name"),
                "role": _row_value(row, "cs_role"),
                "max_conversations": _row_value(row, "cs_max_conversations"),
                "enabled": _row_value(row, "cs_enabled"),
                "accepting": _row_value(row, "cs_accepting"),
            }
        items.append({
            "userId": _row_value(row, "user_id"),
            "username": _row_value(row, "username"),
            "realName": _row_value(row, "real_name") or _row_value(row, "username"),
            "dept": _row_value(row, "dept", ""),
            "platformRole": _row_value(row, "platform_role"),
            "status": _row_value(row, "status"),
            "version": int(_row_value(row, "version", 0)),
            "sessionCount": int(_row_value(row, "session_count", 0) or 0),
            "csAgent": _cs_public(agent),
        })
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    operator: OperatorIdentity = Depends(require_rbac_admin),
):
    """事务性更新平台角色/客服档案，并在提交后清理会话闸键。"""
    body = await request.json()
    tenant_id = _tenant_id(request)
    db = None
    try:
        async with _db() as db:
            outcome = await update_user_in_transaction(
                db,
                user_id=user_id,
                body=body,
                operator=operator,
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


def _audit_item(row: Any) -> dict[str, Any]:
    before = _parse_json(_row_value(row, "before_state", {}))
    after = _parse_json(_row_value(row, "after_state", {}))
    actor_user_id = _row_value(row, "actor_user_id")
    target_user_id = _row_value(row, "target_user_id")
    operator = f"user:{actor_user_id}" if actor_user_id is not None else "service"
    return {
        "id": _row_value(row, "id"),
        "operator": operator,
        "actor": operator,
        "actorUserId": actor_user_id,
        "target": f"user:{target_user_id}",
        "targetUserId": target_user_id,
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
    where = "WHERE tenant_id = :tenant_id"
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if target_user_id is not None:
        where += " AND target_user_id = :user_id"
        params["user_id"] = target_user_id

    async with _db() as db:
        total = int((await db.execute(text(
            f"SELECT COUNT(*) FROM auth.rbac_audits {where}"), params)).scalar() or 0)
        rows = (await db.execute(text(
            f"SELECT id, tenant_id, actor_user_id, target_user_id, action, "
            f"before_state, after_state, result, created_at "
            f"FROM auth.rbac_audits {where} "
            "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"),
            params)).mappings().all()
    return {
        "items": [_audit_item(row) for row in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


__all__ = ["router", "require_rbac_admin", "update_user_in_transaction"]
