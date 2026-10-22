"""知识库目录的 PostgreSQL 持久化与审计。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


class KnowledgeBaseStoreError(RuntimeError):
    """知识库目录不可用或请求违反目录约束。"""


class KnowledgeBaseConflict(KnowledgeBaseStoreError):
    """目录版本冲突或知识库标识已存在。"""


class KnowledgeBaseNotFound(KnowledgeBaseStoreError):
    """目标知识库不存在。"""


def _engine():
    from backend.infra.db import get_memory_engine

    return get_memory_engine()


def _public_row(row: Any) -> dict[str, Any]:
    values = dict(row)
    owners = values.get("owner_depts")
    if isinstance(owners, str):
        owners = json.loads(owners)
    values["owner_depts"] = list(owners or [])
    values["deprecated"] = bool(values.get("deprecated"))
    values["read_only"] = bool(values.get("read_only"))
    values["active"] = values.get("status") == "active"
    return values


def list_knowledge_bases(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """读取动态目录；数据库故障时抛错，授权调用方必须 fail-closed。"""
    query = """
        SELECT kb_id, name, domain, owner_depts, audience, deprecated,
               read_only, alias_for, status, version
        FROM auth.knowledge_bases
    """
    if not include_inactive:
        query += " WHERE status = 'active'"
    query += " ORDER BY name, kb_id"
    with _engine().connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [_public_row(row) for row in rows]


def get_knowledge_base(kb_id: str, *, include_inactive: bool = True) -> dict[str, Any] | None:
    query = """
        SELECT kb_id, name, domain, owner_depts, audience, deprecated,
               read_only, alias_for, status, version
        FROM auth.knowledge_bases WHERE kb_id = :kb_id
    """
    if not include_inactive:
        query += " AND status = 'active'"
    with _engine().connect() as conn:
        row = conn.execute(text(query), {"kb_id": kb_id}).mappings().first()
    return _public_row(row) if row else None


def list_active_departments(tenant_id: str) -> list[dict[str, Any]]:
    return list_departments(tenant_id)


def list_departments(
    tenant_id: str, *, include_inactive: bool = False,
) -> list[dict[str, Any]]:
    if not tenant_id:
        raise KnowledgeBaseStoreError("缺少可信租户身份")
    status_filter = "" if include_inactive else " AND status = 1"
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT code, name, status FROM auth.departments "
            "WHERE tenant_id = :tenant_id" + status_filter + " ORDER BY status DESC, name, code"
        ), {"tenant_id": tenant_id}).mappings().all()
    return [{
        "id": row["code"],
        "label": row["name"] or row["code"],
        "active": row["status"] == 1,
    } for row in rows]


def _audit(
    conn,
    *,
    kb_id: str,
    tenant_id: str,
    actor_id: str,
    action: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    conn.execute(text(
        "INSERT INTO auth.knowledge_base_audits "
        "(kb_id, tenant_id, actor_user_id, action, before_state, after_state) "
        "VALUES (:kb_id, :tenant_id, :actor_id, :action, "
        "CAST(:before_state AS JSONB), CAST(:after_state AS JSONB))"
    ), {
        "kb_id": kb_id,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "action": action,
        "before_state": json.dumps(before, ensure_ascii=False),
        "after_state": json.dumps(after, ensure_ascii=False),
    })


def create_knowledge_base(
    *, kb_id: str, name: str, domain: str, owner_depts: list[str],
    audience: str, tenant_id: str, actor_id: str,
) -> dict[str, Any]:
    try:
        with _engine().begin() as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM auth.knowledge_bases WHERE kb_id = :kb_id"
            ), {"kb_id": kb_id}).first()
            if exists:
                raise KnowledgeBaseConflict("知识库标识已存在")
            row = conn.execute(text(
                "INSERT INTO auth.knowledge_bases "
                "(kb_id, name, domain, owner_depts, audience, created_by, updated_by) "
                "VALUES (:kb_id, :name, :domain, CAST(:owner_depts AS JSONB), "
                ":audience, :actor_id, :actor_id) "
                "RETURNING kb_id, name, domain, owner_depts, audience, deprecated, "
                "read_only, alias_for, status, version"
            ), {
                "kb_id": kb_id, "name": name, "domain": domain,
                "owner_depts": json.dumps(owner_depts), "audience": audience,
                "actor_id": actor_id,
            }).mappings().one()
            result = _public_row(row)
            _audit(conn, kb_id=kb_id, tenant_id=tenant_id, actor_id=actor_id,
                   action="create", before={}, after=result)
            return result
    except KnowledgeBaseConflict:
        raise
    except IntegrityError as exc:
        if getattr(getattr(exc, "orig", None), "pgcode", None) == "23505":
            raise KnowledgeBaseConflict("知识库标识已存在") from exc
        raise


def update_knowledge_base(
    *, kb_id: str, version: int, changes: dict[str, Any],
    tenant_id: str, actor_id: str,
) -> dict[str, Any]:
    allowed = {"name", "domain", "owner_depts", "audience"}
    updates = {key: value for key, value in changes.items() if key in allowed}
    if not updates:
        raise KnowledgeBaseStoreError("没有可更新的知识库字段")
    with _engine().begin() as conn:
        row = conn.execute(text(
            "SELECT kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version FROM auth.knowledge_bases "
            "WHERE kb_id = :kb_id FOR UPDATE"
        ), {"kb_id": kb_id}).mappings().first()
        if not row:
            raise KnowledgeBaseNotFound("知识库不存在")
        before = _public_row(row)
        if before["read_only"]:
            raise KnowledgeBaseStoreError("兼容知识库为只读，不能修改")
        if before["version"] != version:
            raise KnowledgeBaseConflict("知识库已被其他管理员修改，请刷新后重试")

        assignments: list[str] = []
        params: dict[str, Any] = {"kb_id": kb_id, "version": version,
                                  "actor_id": actor_id}
        for key, value in updates.items():
            if key == "owner_depts":
                assignments.append("owner_depts = CAST(:owner_depts AS JSONB)")
                params["owner_depts"] = json.dumps(value)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        assignments.extend(["version = version + 1", "updated_by = :actor_id",
                            "updated_at = now()"])
        after_row = conn.execute(text(
            "UPDATE auth.knowledge_bases SET " + ", ".join(assignments) +
            " WHERE kb_id = :kb_id AND version = :version "
            "RETURNING kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version"
        ), params).mappings().one_or_none()
        if not after_row:
            raise KnowledgeBaseConflict("知识库已被其他管理员修改，请刷新后重试")
        after = _public_row(after_row)
        _audit(conn, kb_id=kb_id, tenant_id=tenant_id, actor_id=actor_id,
               action="update", before=before, after=after)
        return after


def deactivate_knowledge_base(
    *, kb_id: str, version: int, tenant_id: str, actor_id: str,
) -> dict[str, Any]:
    with _engine().begin() as conn:
        row = conn.execute(text(
            "SELECT kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version FROM auth.knowledge_bases "
            "WHERE kb_id = :kb_id FOR UPDATE"
        ), {"kb_id": kb_id}).mappings().first()
        if not row:
            raise KnowledgeBaseNotFound("知识库不存在")
        before = _public_row(row)
        if before["read_only"]:
            raise KnowledgeBaseStoreError("兼容知识库为只读，不能停用")
        if before["version"] != version:
            raise KnowledgeBaseConflict("知识库已被其他管理员修改，请刷新后重试")
        after_row = conn.execute(text(
            "UPDATE auth.knowledge_bases SET status = 'inactive', "
            "version = version + 1, updated_by = :actor_id, updated_at = now() "
            "WHERE kb_id = :kb_id AND version = :version "
            "RETURNING kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version"
        ), {"kb_id": kb_id, "version": version, "actor_id": actor_id}).mappings().one()
        after = _public_row(after_row)
        _audit(conn, kb_id=kb_id, tenant_id=tenant_id, actor_id=actor_id,
               action="deactivate", before=before, after=after)
        return after


def activate_knowledge_base(
    *, kb_id: str, version: int, tenant_id: str, actor_id: str,
) -> dict[str, Any]:
    with _engine().begin() as conn:
        row = conn.execute(text(
            "SELECT kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version FROM auth.knowledge_bases "
            "WHERE kb_id = :kb_id FOR UPDATE"
        ), {"kb_id": kb_id}).mappings().first()
        if not row:
            raise KnowledgeBaseNotFound("知识库不存在")
        before = _public_row(row)
        if before["read_only"]:
            raise KnowledgeBaseStoreError("兼容知识库为只读，不能启用")
        if before["version"] != version:
            raise KnowledgeBaseConflict("知识库已被其他管理员修改，请刷新后重试")
        after_row = conn.execute(text(
            "UPDATE auth.knowledge_bases SET status = 'active', "
            "version = version + 1, updated_by = :actor_id, updated_at = now() "
            "WHERE kb_id = :kb_id AND version = :version "
            "RETURNING kb_id, name, domain, owner_depts, audience, deprecated, "
            "read_only, alias_for, status, version"
        ), {"kb_id": kb_id, "version": version, "actor_id": actor_id}).mappings().one()
        after = _public_row(after_row)
        _audit(conn, kb_id=kb_id, tenant_id=tenant_id, actor_id=actor_id,
               action="activate", before=before, after=after)
        return after
