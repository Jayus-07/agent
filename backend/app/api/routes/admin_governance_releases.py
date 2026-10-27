"""admin_governance_releases.py — 治理配置发布/回滚/导入（Batch 5-2，决策 #08/#14 收口）。

职责边界（V2 发布记录页第一档「治理配置发布」）：
  - 与既有 admin_releases.py（**系统发布 Gate**，ai.release_records，V2 第二档）
    严格区分：本文件治理的是连接声明 + 能力包 + 授权关系的版本化。
  - 快照 payload 与 /admin/mcp/export 同构（D7 红线复用 _export_payload）；
  - 发布 = 确认草稿为生效版本（可追溯，不改变配置本身）；
  - 回滚 = 按快照恢复声明/成员/授权并**生成新版本**记录（原版本保留历史）；
  - 导入 = 从导出包重建声明：连接一律 disabled、不带凭据、不建授权——
    授权与凭据必须由目标环境管理员重新确认。

不做的事（诚实边界）：不回滚凭据；不删除「快照中不存在」的连接/能力包
（差异预览呈现，由管理员显式处置）；工具审核状态不参与发布/回滚。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from psycopg2.extras import Json as PgJson

from backend.app.api.deps import require_admin_user, run_governance_mutation
from backend.app.api.routes.admin_mcp import (
    _MutationConflict,
    _actor_of,
    _audit,
    _bad_request,
    _conflict,
    _execute,
    _export_payload,
    _not_found,
    _query_all,
    _query_one,
    _sanitize_export_url,
    _tenant_of,
)
from backend.shared.logger import logger

router = APIRouter(prefix="/admin/mcp", tags=["GovernanceReleases"])

_CONNECTION_FIELDS = ("base_url", "transport", "auth_type", "network_scope")
_BUNDLE_FIELDS = ("description", "domain")


class ReleaseCreate(BaseModel):
    description: str = Field(default="", max_length=300)


class ImportRequest(BaseModel):
    payload: dict[str, Any]


def _release_view(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    return {
        "id": row["id"],
        "version": row["version"],
        "status": row["status"],
        "description": row["description"],
        "connection_count": len(payload.get("connections") or []),
        "bundle_count": len(payload.get("capability_bundles") or []),
        "created_by": row["created_by"],
        "published_by": row.get("published_by") or "",
        "published_at": (
            row["published_at"].isoformat() if row.get("published_at") else None),
        "created_at": row["created_at"].isoformat(),
    }


def _load_release(release_id: str, tenant: str) -> dict[str, Any] | None:
    return _query_one(
        "SELECT * FROM ai.governance_releases "
        "WHERE id = %(id)s AND tenant_id = %(tenant)s",
        {"id": release_id, "tenant": tenant})


def _diff_payload(release_payload: dict[str, Any],
                  current: dict[str, Any]) -> dict[str, Any]:
    """结构化差异：当前声明 vs 版本快照（方向=回滚到该版本将发生什么）。

    added   = 当前比版本多（回滚**不删除**，仅提示管理员显式处置）
    removed = 版本有、当前无（回滚不自动重建声明，见模块 docstring；授权除外）
    modified= 同名实体字段差异
    """
    def _entry(kind: str, name: str, change: str,
               fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"kind": kind, "name": name, "change": change, "fields": fields or []}

    changes: list[dict[str, Any]] = []
    rel_conns = {c["name"]: c for c in release_payload.get("connections") or []}
    cur_conns = {c["name"]: c for c in current.get("connections") or []}
    for name in sorted(set(rel_conns) | set(cur_conns)):
        r, c = rel_conns.get(name), cur_conns.get(name)
        if r and not c:
            changes.append(_entry("connection", name, "removed"))
        elif c and not r:
            changes.append(_entry("connection", name, "added"))
        else:
            fields = [{"field": f, "release": r[f], "current": c[f]}
                      for f in _CONNECTION_FIELDS if r[f] != c[f]]
            if fields:
                changes.append(_entry("connection", name, "modified", fields))

    rel_bundles = {b["name"]: b for b in release_payload.get("capability_bundles") or []}
    cur_bundles = {b["name"]: b for b in current.get("capability_bundles") or []}
    for name in sorted(set(rel_bundles) | set(cur_bundles)):
        r, c = rel_bundles.get(name), cur_bundles.get(name)
        if r and not c:
            changes.append(_entry("bundle", name, "removed"))
        elif c and not r:
            changes.append(_entry("bundle", name, "added"))
        else:
            fields = [{"field": f, "release": r[f], "current": c[f]}
                      for f in _BUNDLE_FIELDS if r[f] != c[f]]
            for list_field in ("capabilities", "granted_products"):
                if sorted(r[list_field]) != sorted(c[list_field]):
                    fields.append({"field": list_field,
                                   "release": r[list_field],
                                   "current": c[list_field]})
            if fields:
                changes.append(_entry("bundle", name, "modified", fields))

    counts = {"modified": 0, "added": 0, "removed": 0}
    for ch in changes:
        counts[ch["change"]] += 1
    return {"changes": changes, "counts": counts}


def _sync_bundle_member_grants(product_key: str, capabilities: list[str],
                               actor: str, tenant: str, enable: bool) -> None:
    """bundle 授权对齐时同步动态成员 ai.mcp_tool_grants（与 grant_bundle
    同款 SQL——运行时执行契约的事实源，不同步则回滚对动态工具不生效）。"""
    for cap in capabilities:
        if not str(cap).startswith("mcp."):
            continue
        slug = str(cap)[len("mcp."):]
        if enable:
            g = _query_one(
                "SELECT g.id FROM ai.mcp_tool_grants g JOIN ai.mcp_tools t "
                "ON g.tool_id = t.id WHERE t.capability_slug = %(slug)s "
                "AND g.product_key = %(pk)s AND g.tenant_id = %(tenant)s",
                {"slug": slug, "pk": product_key, "tenant": tenant})
            if g is None:
                _execute(
                    "INSERT INTO ai.mcp_tool_grants "
                    "(tool_id, product_key, enabled, granted_by, tenant_id) "
                    "SELECT t.id, %(pk)s, true, %(actor)s, %(tenant)s "
                    "FROM ai.mcp_tools t WHERE t.capability_slug = %(slug)s",
                    {"pk": product_key, "actor": actor, "tenant": tenant,
                     "slug": slug})
            else:
                _execute(
                    "UPDATE ai.mcp_tool_grants SET enabled = true, "
                    "revoked_at = NULL, granted_by = %(actor)s "
                    "WHERE id = %(gid)s",
                    {"actor": actor, "gid": g["id"]})
        else:
            _execute(
                "UPDATE ai.mcp_tool_grants g SET enabled = false, "
                "revoked_at = now() FROM ai.mcp_tools t "
                "WHERE g.tool_id = t.id AND t.capability_slug = %(slug)s "
                "AND g.product_key = %(pk)s AND g.tenant_id = %(tenant)s",
                {"slug": slug, "pk": product_key, "tenant": tenant})


@router.get("/releases")
async def list_releases(request: Request):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    rows = await asyncio.to_thread(
        _query_all,
        "SELECT * FROM ai.governance_releases WHERE tenant_id = %(t)s "
        "ORDER BY version DESC",
        {"t": tenant})
    return {"releases": [_release_view(r) for r in rows]}


@router.post("/releases")
async def create_release(request: Request, body: ReleaseCreate):
    """创建发布草稿：快照当前声明（复用导出载荷，凭据红线同源）。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)

    def _write() -> dict[str, Any]:
        payload = _export_payload(tenant)
        if (not payload["connections"]) and (not payload["capability_bundles"]):
            raise _MutationConflict("当前治理配置为空，无可快照内容")
        seq = _query_one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next "
            "FROM ai.governance_releases WHERE tenant_id = %(t)s",
            {"t": tenant})
        release_id = f"gov_rel_{uuid.uuid4().hex[:12]}"
        _execute(
            "INSERT INTO ai.governance_releases "
            "(id, version, tenant_id, status, description, payload, created_by) "
            "VALUES (%(id)s, %(ver)s, %(tenant)s, 'draft', %(desc)s, "
            "%(payload)s, %(actor)s)",
            {"id": release_id, "ver": seq["next"], "tenant": tenant,
             "desc": body.description, "payload": PgJson(payload),
             "actor": actor})
        _audit("governance_release_create", {
            "release_id": release_id, "version": seq["next"],
            "connections": len(payload["connections"]),
            "bundles": len(payload["capability_bundles"]),
            "rejected": len(payload["rejected_connections"]),
        }, user_id=actor, tenant_id=tenant)
        return _release_view(_query_one(
            "SELECT * FROM ai.governance_releases WHERE id = %(id)s",
            {"id": release_id}))

    try:
        return await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "governance_release_create",
            {"description": body.description}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))


@router.get("/releases/{release_id}/diff")
async def diff_release(request: Request, release_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    row = await asyncio.to_thread(_load_release, release_id, tenant)
    if row is None:
        return _not_found("发布版本不存在")
    current = await asyncio.to_thread(_export_payload, tenant)
    return await asyncio.to_thread(_diff_payload, row["payload"], current)


@router.post("/releases/{release_id}/publish")
async def publish_release(request: Request, release_id: str):
    """发布：草稿 → 生效版本（确认语义，不改变配置本身）。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_release, release_id, tenant)
    if row is None:
        return _not_found("发布版本不存在")
    if row["status"] != "draft":
        return _conflict(f"仅 draft 可发布，当前状态: {row['status']}")

    def _write() -> dict[str, Any]:
        _execute(
            "UPDATE ai.governance_releases SET status = 'published', "
            "published_by = %(actor)s, published_at = now() "
            "WHERE id = %(id)s AND status = 'draft'",
            {"actor": actor, "id": release_id})
        _audit("governance_release_publish", {
            "release_id": release_id, "version": row["version"],
        }, user_id=actor, tenant_id=tenant)
        return _release_view(_query_one(
            "SELECT * FROM ai.governance_releases WHERE id = %(id)s",
            {"id": release_id}))

    return await asyncio.to_thread(
        run_governance_mutation, request, operator,
        "governance_release_publish", {"release_id": release_id}, _write)


@router.post("/releases/{release_id}/rollback")
async def rollback_release(request: Request, release_id: str):
    """回滚到已发布版本：恢复声明/成员/授权并**生成新版本**记录。

    边界：不恢复凭据；不删除快照中不存在的连接/能力包；工具审核状态不回滚。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_release, release_id, tenant)
    if row is None:
        return _not_found("发布版本不存在")
    if row["status"] != "published":
        return _conflict("仅已发布版本可回滚")
    payload = row["payload"]

    def _write() -> dict[str, Any]:
        restored_connections = 0
        for c in payload.get("connections") or []:
            # 双保险：快照创建时已净化；此处再验一次，凭据样式不落库
            if _sanitize_export_url(str(c.get("base_url") or "")) is None:
                logger.warning(
                    "[Releases] 回滚快照含不可净化 URL，跳过连接 %s", c.get("name"))
                continue
            restored_connections += _execute(
                "UPDATE ai.mcp_connections SET base_url = %(url)s, "
                "transport = %(tr)s, auth_type = %(auth)s, "
                "network_scope = %(scope)s, updated_by = %(actor)s, "
                "updated_at = now() "
                "WHERE name = %(name)s AND tenant_id = %(tenant)s",
                {"url": c["base_url"], "tr": c["transport"],
                 "auth": c["auth_type"], "scope": c["network_scope"],
                 "actor": actor, "name": c["name"], "tenant": tenant})

        restored_bundles = 0
        for b in payload.get("capability_bundles") or []:
            bundle = _query_one(
                "SELECT id FROM ai.capability_bundles WHERE name = %(name)s",
                {"name": b["name"]})
            if bundle is None:
                continue
            restored_bundles += 1
            _execute(
                "UPDATE ai.capability_bundles SET description = %(d)s, "
                "domain = %(dom)s, updated_by = %(actor)s, updated_at = now() "
                "WHERE id = %(bid)s",
                {"d": b["description"], "dom": b["domain"],
                 "actor": actor, "bid": bundle["id"]})
            _execute(
                "DELETE FROM ai.capability_bundle_items WHERE bundle_id = %(bid)s",
                {"bid": bundle["id"]})
            for cap in b.get("capabilities") or []:
                _execute(
                    "INSERT INTO ai.capability_bundle_items "
                    "(bundle_id, capability, added_by) VALUES "
                    "(%(bid)s, %(cap)s, %(actor)s) ON CONFLICT DO NOTHING",
                    {"bid": bundle["id"], "cap": cap, "actor": actor})
            # 授权精确对齐快照：多余的有效授权禁用，快照授权启用/补建
            target_products = {str(p) for p in b.get("granted_products") or []}
            for g in _query_all(
                    "SELECT id, product_key, enabled, revoked_at FROM "
                    "ai.product_bundle_grants WHERE bundle_id = %(bid)s "
                    "AND tenant_id = %(tenant)s",
                    {"bid": bundle["id"], "tenant": tenant}):
                active = bool(g["enabled"]) and g["revoked_at"] is None
                pk = str(g["product_key"])
                if pk in target_products and not active:
                    _execute(
                        "UPDATE ai.product_bundle_grants SET enabled = true, "
                        "revoked_at = NULL, granted_by = %(actor)s, "
                        "granted_at = now() WHERE id = %(gid)s",
                        {"actor": actor, "gid": g["id"]})
                elif pk not in target_products and active:
                    _execute(
                        "UPDATE ai.product_bundle_grants SET enabled = false, "
                        "revoked_at = now() WHERE id = %(gid)s",
                        {"gid": g["id"]})
            for pk in sorted(target_products):
                _sync_bundle_member_grants(
                    pk, b.get("capabilities") or [], actor, tenant, enable=True)

        _audit("governance_release_rollback", {
            "release_id": release_id, "version": row["version"],
            "restored_connections": restored_connections,
            "restored_bundles": restored_bundles,
        }, user_id=actor, tenant_id=tenant)
        # 回滚生成新版本记录（原型语义：快照恢复并生成新版本）
        seq = _query_one(
            "SELECT COALESCE(MAX(version), 0) + 1 AS next "
            "FROM ai.governance_releases WHERE tenant_id = %(t)s",
            {"t": tenant})
        new_id = f"gov_rel_{uuid.uuid4().hex[:12]}"
        _execute(
            "INSERT INTO ai.governance_releases "
            "(id, version, tenant_id, status, description, payload, "
            " created_by, published_by, published_at) "
            "VALUES (%(id)s, %(ver)s, %(tenant)s, 'published', %(desc)s, "
            "%(payload)s, %(actor)s, %(actor)s, now())",
            {"id": new_id, "ver": seq["next"], "tenant": tenant,
             "desc": f"回滚自 v{row['version']}: {row['description']}".rstrip(": "),
             "payload": PgJson(payload), "actor": actor})
        return {
            "rolled_back_to": row["version"],
            "new_release_id": new_id,
            "new_version": seq["next"],
            "restored_connections": restored_connections,
            "restored_bundles": restored_bundles,
        }

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "governance_release_rollback",
            {"release_id": release_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    # 授权行可能变化 → 刷新两套快照（失败不影响已提交的回滚）
    await _refresh_registries()
    return result


async def _refresh_registries() -> None:
    from backend.app.api.routes.admin_mcp import _refresh_runtime
    from backend.infra.bundle_registry import store as bundle_store

    try:
        await _refresh_runtime()
    except Exception:  # noqa: BLE001 — 刷新失败不回滚已提交的事务
        logger.warning("[Releases] mcp_registry 刷新失败", exc_info=True)
    try:
        await bundle_store.refresh()
    except Exception:  # noqa: BLE001
        logger.warning("[Releases] bundle_registry 刷新失败", exc_info=True)


@router.post("/import")
async def import_configuration(request: Request, body: ImportRequest):
    """从导出包重建声明（纯增量、安全侧倾斜）：
    - 连接：一律 enabled=false、无凭据（目标环境重配后按需启用）；
    - 能力包：重建成员，**不建任何授权行**（导入方管理员确认后授权）；
    - 名称冲突跳过（不覆盖现网声明），skipped/rejected 透明返回。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    payload = body.payload or {}
    if payload.get("version") != 1:
        return _bad_request("导入包 version 必须为 1")

    def _write() -> dict[str, Any]:
        created_conns: list[str] = []
        skipped: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        redacted: list[dict[str, Any]] = []
        for c in payload.get("connections") or []:
            name = str(c.get("name") or "")
            if not name:
                continue
            sanitized = _sanitize_export_url(str(c.get("base_url") or ""))
            if sanitized is None:
                rejected.append({
                    "name": name,
                    "reason": "URL 内嵌凭据且无法安全净化，请重新配置地址",
                })
                continue
            base_url = sanitized[0]
            if sanitized[1]:
                redacted.append({"name": name, "removed": sanitized[1]})
            dup = _query_one(
                "SELECT 1 AS one FROM ai.mcp_connections "
                "WHERE name = %(name)s AND tenant_id = %(tenant)s",
                {"name": name, "tenant": tenant})
            if dup is not None:
                skipped.append({"name": name, "reason": "连接名已存在"})
                continue
            _execute(
                "INSERT INTO ai.mcp_connections "
                "(id, name, base_url, transport, auth_type, network_scope, "
                " enabled, status, tenant_id, created_by, updated_by) "
                "VALUES (%(id)s, %(name)s, %(url)s, %(tr)s, %(auth)s, "
                "%(scope)s, false, 'never_connected', %(tenant)s, "
                "%(actor)s, %(actor)s)",
                {"id": f"mcp_conn_{uuid.uuid4().hex[:12]}", "name": name,
                 "url": base_url,
                 "tr": str(c.get("transport") or "streamable_http"),
                 "auth": str(c.get("auth_type") or "none"),
                 "scope": str(c.get("network_scope") or "public"),
                 "tenant": tenant, "actor": actor})
            created_conns.append(name)

        created_bundles: list[str] = []
        bundle_skipped: list[dict[str, Any]] = []
        for b in payload.get("capability_bundles") or []:
            name = str(b.get("name") or "")
            if not name:
                continue
            dup = _query_one(
                "SELECT id FROM ai.capability_bundles WHERE name = %(name)s",
                {"name": name})
            if dup is not None:
                bundle_skipped.append({"name": name, "reason": "能力包名已存在"})
                continue
            bundle_id = f"cap_bundle_{uuid.uuid4().hex[:12]}"
            _execute(
                "INSERT INTO ai.capability_bundles "
                "(id, name, description, domain, enabled, created_by, "
                " updated_by) VALUES (%(id)s, %(name)s, %(desc)s, %(dom)s, "
                "true, %(actor)s, %(actor)s)",
                {"id": bundle_id, "name": name,
                 "desc": str(b.get("description") or ""),
                 "dom": str(b.get("domain") or "general"), "actor": actor})
            for cap in b.get("capabilities") or []:
                _execute(
                    "INSERT INTO ai.capability_bundle_items "
                    "(bundle_id, capability, added_by) VALUES "
                    "(%(bid)s, %(cap)s, %(actor)s) ON CONFLICT DO NOTHING",
                    {"bid": bundle_id, "cap": str(cap), "actor": actor})
            created_bundles.append(name)

        _audit("mcp_configuration_import", {
            "created_connections": len(created_conns),
            "created_bundles": len(created_bundles),
            "skipped": len(skipped) + len(bundle_skipped),
            "rejected": len(rejected),
        }, user_id=actor, tenant_id=tenant)
        return {
            "created_connections": created_conns,
            "created_bundles": created_bundles,
            "skipped": skipped + bundle_skipped,
            "rejected": rejected,
            "redacted": redacted,
        }

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "mcp_configuration_import", {}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    # 导入的连接 disabled 且无凭据，不进运行时；bundle_registry 刷新对齐成员
    await _refresh_registries()
    return result


__all__ = ["router"]
