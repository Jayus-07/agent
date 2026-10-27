"""admin_bundles — 能力包管理 API（Batch 2，ADR-0011 D5 扩展）。

能力包 = 静态 capability 与动态 mcp.* 的授权聚合单元。写路径走
run_governance_mutation（幂等键 + 审计），全部 require_admin_user。

授权语义：
  - 授权/撤销 bundle 只改 ai.product_bundle_grants（事实源）；
  - 动态 mcp.* 成员的执行契约仍由 ai.mcp_tool_grants 承载——授权 bundle
    时**自动为其中动态成员补建 grants**（撤权同理禁用），保证 bundle
    变更对运行链即时生效（写后 refresh_now）；
  - 静态成员经 bundle_registry 快照进入 product_allows_capability 白名单
    （15s 轮询收敛，写后本进程立即生效）。
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import require_admin_user, run_governance_mutation
from backend.app.api.routes.admin_mcp import (
    _MutationConflict,
    _actor_of,
    _audit,
    _db,
    _execute,
    _query_all,
    _query_one,
    _tenant_of,
)
from backend.core.product_context import DOMAIN_LABELS
from backend.shared.logger import logger

router = APIRouter(prefix="/admin/capability-bundles", tags=["CapabilityBundles"])

# 与工具治理 governance.domain 共用同一业务域词汇表（单一事实源）
_VALID_DOMAINS = DOMAIN_LABELS


class BundleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    domain: str = Field(default="general", max_length=32)
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class BundleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    # 提供时整体替换成员集合
    capabilities: list[str] | None = Field(default=None, max_length=100)


class BundleGrantRequest(BaseModel):
    product_key: Literal["agent", "customer_service"]
    revoke: bool = False


def _load_bundle(bundle_id: str, tenant_id: str) -> dict | None:
    row = _query_one(
        "SELECT id, name, description, domain, enabled, version, "
        "created_at, updated_at FROM ai.capability_bundles "
        "WHERE id = %(bid)s",
        {"bid": bundle_id})
    if row is None:
        return None
    items = _query_all(
        "SELECT capability FROM ai.capability_bundle_items "
        "WHERE bundle_id = %(bid)s ORDER BY capability",
        {"bid": bundle_id})
    grants = _query_all(
        "SELECT product_key, enabled, granted_by, granted_at, revoked_at "
        "FROM ai.product_bundle_grants WHERE bundle_id = %(bid)s "
        "AND tenant_id = %(tenant)s ORDER BY granted_at DESC",
        {"bid": bundle_id, "tenant": tenant_id})
    row["capabilities"] = [str(i["capability"]) for i in items]
    row["grants"] = [{
        "product_key": g["product_key"],
        "active": bool(g["enabled"] and not g["revoked_at"]),
        "granted_by": g["granted_by"],
        "granted_at": g["granted_at"].isoformat(),
        "revoked_at": g["revoked_at"].isoformat() if g["revoked_at"] else None,
    } for g in grants]
    return row


def _bundle_rows(tenant_id: str) -> list[dict]:
    rows = _query_all(
        "SELECT id, name, description, domain, enabled, version, "
        "updated_at FROM ai.capability_bundles ORDER BY name")
    if not rows:
        return []
    items = _query_all(
        "SELECT bundle_id, capability FROM ai.capability_bundle_items")
    grants = _query_all(
        "SELECT bundle_id, product_key FROM ai.product_bundle_grants "
        "WHERE enabled AND revoked_at IS NULL AND tenant_id = %(tenant)s",
        {"tenant": tenant_id})
    items_by_bundle: dict[str, list[str]] = {}
    for i in items:
        items_by_bundle.setdefault(str(i["bundle_id"]), []).append(
            str(i["capability"]))
    grants_by_bundle: dict[str, list[str]] = {}
    for g in grants:
        grants_by_bundle.setdefault(str(g["bundle_id"]), []).append(
            str(g["product_key"]))
    for row in rows:
        bid = str(row["id"])
        row["capabilities"] = items_by_bundle.get(bid, [])
        row["granted_products"] = grants_by_bundle.get(bid, [])
        row["updated_at"] = row["updated_at"].isoformat()
    return rows


def _validate_members(capabilities: list[str]) -> None:
    """成员名校验：格式 + 动态成员须已注册（静态成员由一致性检查对账）。"""
    from backend.infra.mcp_registry import store as mcp_store
    from backend.infra.mcp_registry.specs import is_valid_slug

    seen: set[str] = set()
    for cap in capabilities:
        name = str(cap or "").strip().lower()
        if not name or len(name) > 128 or name in seen:
            raise _MutationConflict(f"非法或重复的 capability 成员: {cap!r}")
        seen.add(name)
        if name.startswith("mcp."):
            slug = name[len("mcp."):]
            if not is_valid_slug(slug):
                raise _MutationConflict(f"动态 capability 名非法: {name}")
            if mcp_store.get_runtime(name) is None:
                raise _MutationConflict(
                    f"动态 capability 未在运行时生效: {name}")


@router.get("")
async def list_bundles(request: Request):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    rows = await _run_db(_bundle_rows, tenant)
    return {"count": len(rows), "bundles": rows}


@router.get("/{bundle_id}")
async def get_bundle(request: Request, bundle_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    row = await _run_db(_load_bundle, bundle_id, tenant)
    if row is None:
        return _not_found("能力包不存在")
    return row


class _BadRequest(Exception):
    pass


def _not_found(message: str = "资源不存在"):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404,
                        content={"code": "NOT_FOUND", "message": message})


async def _run_db(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


@router.post("")
async def create_bundle(request: Request, body: BundleCreate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    if body.domain not in _VALID_DOMAINS:
        return _bad_request(f"domain 非法: {body.domain}（允许 {_VALID_DOMAINS}）")
    try:
        _validate_members(body.capabilities)
    except _MutationConflict as exc:
        return _bad_request(str(exc))
    bundle_id = f"cap_bundle_{uuid.uuid4().hex[:12]}"

    def _write() -> dict[str, Any]:
        dup = _query_one(
            "SELECT 1 AS one FROM ai.capability_bundles WHERE name = %(name)s",
            {"name": body.name})
        if dup:
            raise _MutationConflict(f"能力包名已存在: {body.name}")
        _execute(
            "INSERT INTO ai.capability_bundles "
            "(id, name, description, domain, created_by, updated_by) "
            "VALUES (%(id)s, %(name)s, %(desc)s, %(domain)s, %(actor)s, %(actor)s)",
            {"id": bundle_id, "name": body.name, "desc": body.description,
             "domain": body.domain, "actor": actor})
        for cap in body.capabilities:
            _execute(
                "INSERT INTO ai.capability_bundle_items "
                "(bundle_id, capability, added_by) VALUES (%(bid)s, %(cap)s, %(a)s)",
                {"bid": bundle_id, "cap": cap.strip().lower(), "a": actor})
        _audit("capability_bundle_create", {
            "bundle_id": bundle_id, "name": body.name,
            "domain": body.domain, "members": len(body.capabilities),
        }, user_id=actor, tenant_id=tenant)
        return {"id": bundle_id}

    try:
        result = await _mutate(request, operator, "capability_bundle_create",
                               {"name": body.name}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_bundle_runtime()
    return result


@router.put("/{bundle_id}")
async def update_bundle(request: Request, bundle_id: str, body: BundleUpdate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await _run_db(_load_bundle, bundle_id, tenant)
    if row is None:
        return _not_found("能力包不存在")
    if body.domain is not None and body.domain not in _VALID_DOMAINS:
        return _bad_request(f"domain 非法: {body.domain}")
    if body.capabilities is not None:
        try:
            _validate_members(body.capabilities)
        except _MutationConflict as exc:
            return _bad_request(str(exc))

    def _write() -> dict[str, Any]:
        name = body.name if body.name is not None else row["name"]
        if body.name is not None and body.name != row["name"]:
            dup = _query_one(
                "SELECT 1 AS one FROM ai.capability_bundles "
                "WHERE name = %(name)s AND id <> %(bid)s",
                {"name": body.name, "bid": bundle_id})
            if dup:
                raise _MutationConflict(f"能力包名已存在: {body.name}")
        _execute(
            "UPDATE ai.capability_bundles SET name = %(name)s, "
            "description = %(desc)s, domain = %(domain)s, "
            "enabled = COALESCE(%(enabled)s, enabled), "
            "version = version + 1, updated_by = %(actor)s, updated_at = now() "
            "WHERE id = %(bid)s",
            {"name": name,
             "desc": body.description if body.description is not None
             else row["description"],
             "domain": body.domain if body.domain is not None else row["domain"],
             "enabled": body.enabled, "actor": actor, "bid": bundle_id})
        if body.capabilities is not None:
            _execute(
                "DELETE FROM ai.capability_bundle_items WHERE bundle_id = %(bid)s",
                {"bid": bundle_id})
            for cap in body.capabilities:
                _execute(
                    "INSERT INTO ai.capability_bundle_items "
                    "(bundle_id, capability, added_by) "
                    "VALUES (%(bid)s, %(cap)s, %(a)s)",
                    {"bid": bundle_id, "cap": cap.strip().lower(), "a": actor})
        _audit("capability_bundle_update", {
            "bundle_id": bundle_id,
            "members": len(body.capabilities) if body.capabilities is not None
            else len(row["capabilities"]),
            "enabled": body.enabled,
        }, user_id=actor, tenant_id=tenant)
        return {"id": bundle_id, "updated": True}

    try:
        result = await _mutate(request, operator, "capability_bundle_update",
                               {"bundle_id": bundle_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_bundle_runtime()
    return result


@router.post("/{bundle_id}/grants")
async def grant_bundle(request: Request, bundle_id: str,
                       body: BundleGrantRequest):
    """授权/撤销能力包给产品入口。

    撤销立即生效（事实源行禁用/打 revoked 时间 + 写后刷新）；bundle 中
    的动态成员同步禁用其 ai.mcp_tool_grants 行——写操作的出网强一致
    复核读 grants 表，撤销后写调用立即被拦截。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await _run_db(_load_bundle, bundle_id, tenant)
    if row is None:
        return _not_found("能力包不存在")

    if not body.revoke:
        # 决策 #15 业务域硬隔离：跨域专属能力不可授权给该产品入口。
        # 静态成员按能力名根判断；动态 mcp.* 工具按 bundle.domain 与
        # 审核登记的 governance.domain 双层判断（授权端点是跨域误授权
        # 的服务端拒绝点）。
        from backend.core.product_context import (
            _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS,
            domain_exclusive_forbidden_roots,
        )

        forbidden = domain_exclusive_forbidden_roots(body.product_key)
        offending = sorted({
            str(cap) for cap in row["capabilities"]
            if str(cap).split(".", 1)[0] in forbidden
        })
        bundle_domain = str(row.get("domain") or "")
        if forbidden & _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS.get(bundle_domain, frozenset()):
            offending.append(f"bundle.domain={bundle_domain}")
        if offending:
            return _bad_request(
                "跨域专属能力不可授权给该产品入口（业务域硬隔离）："
                + "、".join(offending))

        # 动态成员逐工具校验审核登记的业务域（bundle.domain 之外的精细层：
        # domain 标签无关的 bundle 也不得夹带跨域专属工具）
        mcp_members = [str(c) for c in row["capabilities"]
                       if str(c).startswith("mcp.")]
        if mcp_members:
            slugs = [c[len("mcp."):] for c in mcp_members]
            tool_rows = await _run_db(
                _query_all,
                "SELECT capability_slug, governance FROM ai.mcp_tools "
                "WHERE capability_slug = ANY(%(slugs)s) "
                "AND tenant_id = %(tenant)s",
                {"slugs": slugs, "tenant": tenant})
            offending_tools = sorted({
                f"mcp.{r['capability_slug']}(domain={tool_domain})"
                for r in tool_rows
                for tool_domain in [
                    str((r.get("governance") or {}).get("domain") or "")]
                if tool_domain and forbidden & _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS.get(
                    tool_domain, frozenset())
            })
            if offending_tools:
                return _bad_request(
                    "跨域专属工具不可授权给该产品入口（业务域硬隔离）："
                    + "、".join(offending_tools))

    def _write() -> dict[str, Any]:
        if body.revoke:
            _execute(
                "UPDATE ai.product_bundle_grants SET enabled = false, "
                "revoked_at = now(), updated_by = COALESCE(updated_by, '') "
                "WHERE bundle_id = %(bid)s AND product_key = %(pk)s "
                "AND tenant_id = %(tenant)s AND revoked_at IS NULL",
                {"bid": bundle_id, "pk": body.product_key, "tenant": tenant})
            _execute(
                "UPDATE ai.product_bundle_grants SET enabled = false, "
                "revoked_at = now() WHERE bundle_id = %(bid)s "
                "AND product_key = %(pk)s AND tenant_id = %(tenant)s",
                {"bid": bundle_id, "pk": body.product_key, "tenant": tenant})
            # bundle 内动态成员的执行授权同步撤销（强一致撤权的事实源）
            for cap in row["capabilities"]:
                if cap.startswith("mcp."):
                    _execute(
                        "UPDATE ai.mcp_tool_grants g SET enabled = false, "
                        "revoked_at = now() FROM ai.mcp_tools t "
                        "WHERE g.tool_id = t.id "
                        "AND t.capability_slug = %(slug)s "
                        "AND g.product_key = %(pk)s AND g.tenant_id = %(tenant)s",
                        {"slug": cap[len("mcp."):], "pk": body.product_key,
                         "tenant": tenant})
        else:
            dup = _query_one(
                "SELECT id, enabled, revoked_at FROM ai.product_bundle_grants "
                "WHERE bundle_id = %(bid)s AND product_key = %(pk)s "
                "AND tenant_id = %(tenant)s",
                {"bid": bundle_id, "pk": body.product_key, "tenant": tenant})
            if dup is None:
                _execute(
                    "INSERT INTO ai.product_bundle_grants "
                    "(bundle_id, product_key, granted_by, tenant_id) "
                    "VALUES (%(bid)s, %(pk)s, %(actor)s, %(tenant)s)",
                    {"bid": bundle_id, "pk": body.product_key,
                     "actor": actor, "tenant": tenant})
            else:
                _execute(
                    "UPDATE ai.product_bundle_grants SET enabled = true, "
                    "revoked_at = NULL, granted_by = %(actor)s, "
                    "granted_at = now() WHERE id = %(gid)s",
                    {"gid": dup["id"], "actor": actor})
            # bundle 内动态成员补建执行授权（幂等：存在即启用）
            for cap in row["capabilities"]:
                if cap.startswith("mcp."):
                    g = _query_one(
                        "SELECT g.id, g.enabled, g.revoked_at "
                        "FROM ai.mcp_tool_grants g JOIN ai.mcp_tools t "
                        "ON g.tool_id = t.id "
                        "WHERE t.capability_slug = %(slug)s "
                        "AND g.product_key = %(pk)s AND g.tenant_id = %(tenant)s",
                        {"slug": cap[len("mcp."):], "pk": body.product_key,
                         "tenant": tenant})
                    if g is None:
                        _execute(
                            "INSERT INTO ai.mcp_tool_grants "
                            "(tool_id, product_key, granted_by, tenant_id) "
                            "SELECT t.id, %(pk)s, %(actor)s, %(tenant)s "
                            "FROM ai.mcp_tools t "
                            "WHERE t.capability_slug = %(slug)s",
                            {"pk": body.product_key, "actor": actor,
                             "tenant": tenant, "slug": cap[len("mcp."):]})
                    else:
                        _execute(
                            "UPDATE ai.mcp_tool_grants SET enabled = true, "
                            "revoked_at = NULL, granted_by = %(actor)s "
                            "WHERE id = %(gid)s",
                            {"gid": g["id"], "actor": actor})
        _audit("capability_bundle_grant", {
            "bundle_id": bundle_id, "product_key": body.product_key,
            "revoke": body.revoke, "members": len(row["capabilities"]),
        }, user_id=actor, tenant_id=tenant)
        return {"bundle_id": bundle_id, "product_key": body.product_key,
                "revoked": body.revoke}

    try:
        result = await _mutate(request, operator, "capability_bundle_grant",
                               {"bundle_id": bundle_id,
                                "product_key": body.product_key,
                                "revoke": body.revoke}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    # bundle 授权牵动动态成员 grants → 两套快照都刷新
    await _refresh_bundle_runtime()
    from backend.app.api.routes.admin_mcp import _refresh_runtime

    await _refresh_runtime()
    return result


def _bad_request(message: str, code: str = "INVALID_PARAM"):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400,
                        content={"code": code, "message": message})


def _conflict(message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409,
                        content={"code": "CONFLICT", "message": message})


async def _mutate(request: Request, operator, operation: str,
                  payload: dict, callback):
    import asyncio

    return await asyncio.to_thread(
        run_governance_mutation, request, operator, operation, payload,
        callback)


async def _refresh_bundle_runtime() -> None:
    from backend.infra.bundle_registry import store as bundle_store

    try:
        await bundle_store.refresh()
    except Exception:  # noqa: BLE001 — 刷新失败由轮询兜底
        logger.warning("[AdminBundles] 刷新能力包快照失败（轮询会重试）",
                       exc_info=True)


__all__ = ["router"]
