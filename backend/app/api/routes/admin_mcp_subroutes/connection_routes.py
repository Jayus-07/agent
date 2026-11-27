"""admin_mcp_subroutes/connection_routes.py — MCP 连接管理与发现路由。"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from psycopg2.extras import Json as PgJson, RealDictCursor

from backend.app.api.deps import require_admin_user, run_governance_mutation
from backend.app.api.routes.admin_mcp_subroutes.common import (
    _CONNECTION_SELECT,
    _actor_of,
    _audit,
    _bad_request,
    _classify_mcp_error,
    _conflict,
    _conn_headers,
    _connection_secret,
    _connection_view,
    _db,
    _embedded_credential_reason,
    _execute,
    _load_connection,
    _load_tool,
    _mark_connection_status,
    _not_found,
    _probe_http_api,
    _query_all,
    _query_one,
    _record_probe_result,
    _refresh_runtime,
    _tenant_of,
    _upsert_credential,
    _validate_connection_url,
    _validate_egress_allowlist,
)
from backend.app.api.routes.admin_mcp_subroutes.dynamic import (
    current_audit,
    current_connection_view,
    current_execute,
    current_load_connection,
    current_load_tool,
    current_probe_http_api,
    current_query_all,
    current_query_one,
    current_run_governance_mutation,
)
from backend.app.api.routes.admin_mcp_subroutes.models import (
    ConnectionCreate,
    ConnectionUpdate,
    HttpToolInvocationUpdateRequest,
    HttpToolRegisterRequest,
    _MutationConflict,
)
from backend.infra.mcp_client import list_tools as mcp_list_tools

from backend.app.api.routes.admin_mcp_subroutes.routes import router


@router.get("/connections")
async def list_connections(request: Request):
    operator = await require_admin_user(request)
    query_all = current_query_all(_query_all)
    connection_view_fn = current_connection_view(_connection_view)
    rows = await asyncio.to_thread(
        query_all,
        f"{_CONNECTION_SELECT} WHERE c.tenant_id = %(tenant)s "
        "ORDER BY c.created_at DESC",
        {"tenant": _tenant_of(operator, request)},
    )
    return {"connections": [connection_view_fn(r) for r in rows]}


@router.post("/connections")
async def create_connection(request: Request, body: ConnectionCreate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    try:
        _validate_connection_url(body.base_url, body.network_scope)
        _validate_egress_allowlist(body.base_url)
    except ValueError as exc:
        return _bad_request(str(exc))
    cred_reason = _embedded_credential_reason(body.base_url)
    if cred_reason:
        return _bad_request(cred_reason)

    def _write() -> dict[str, Any]:
        query_one = current_query_one(_query_one)
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        conn_id = f"mcp_conn_{uuid.uuid4().hex[:12]}"
        dup = query_one(
            "SELECT 1 AS one FROM ai.mcp_connections "
            "WHERE name = %(name)s AND tenant_id = %(tenant)s",
            {"name": body.name, "tenant": tenant})
        if dup:
            raise _MutationConflict(f"连接名已存在: {body.name}")
        execute_fn(
            """
            INSERT INTO ai.mcp_connections
                (id, name, base_url, transport, auth_type, network_scope,
                 enabled, status, tenant_id, created_by, updated_by)
            VALUES (%(id)s, %(name)s, %(url)s, %(transport)s, %(auth)s,
                    %(scope)s, %(enabled)s, 'never_connected',
                    %(tenant)s, %(actor)s, %(actor)s)
            """,
            {"id": conn_id, "name": body.name, "url": body.base_url,
             "transport": body.transport, "auth": body.auth_type,
             "scope": body.network_scope,
             "enabled": body.enabled, "tenant": tenant, "actor": actor})
        if body.auth_type == "static_bearer" and body.secret:
            _upsert_credential(conn_id, body.secret, actor)
        audit_fn("mcp_connection_create", {
            "connection_id": conn_id, "name": body.name,
            "transport": body.transport,
            "network_scope": body.network_scope,
            "credential_configured": bool(body.secret),
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id}

    try:
        result = await asyncio.to_thread(
            current_run_governance_mutation(run_governance_mutation), request, operator,
            "mcp_connection_create",
            {"name": body.name, "base_url": body.base_url}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.get("/connections/{conn_id}")
async def get_connection(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    load_connection_fn = current_load_connection(_load_connection)
    connection_view_fn = current_connection_view(_connection_view)
    row = await asyncio.to_thread(
        load_connection_fn, conn_id, _tenant_of(operator, request))
    if row is None:
        return _not_found("连接不存在")
    return connection_view_fn(row)


@router.put("/connections/{conn_id}")
async def update_connection(request: Request, conn_id: str,
                            body: ConnectionUpdate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    row = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    next_url = body.base_url or row["base_url"]
    next_scope = body.network_scope or row["network_scope"]
    if body.base_url is not None:
        cred_reason = _embedded_credential_reason(body.base_url)
        if cred_reason:
            return _bad_request(cred_reason)
    if body.base_url is not None or body.network_scope is not None:
        try:
            _validate_connection_url(next_url, next_scope)
            if body.base_url is not None:
                _validate_egress_allowlist(next_url)
        except ValueError as exc:
            return _bad_request(str(exc))

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        enabled = row["enabled"] if body.enabled is None else body.enabled
        status = row["status"]
        if body.enabled is not None and body.enabled != row["enabled"]:
            status = "connected" if body.enabled else "disabled"
        execute_fn(
            """
            UPDATE ai.mcp_connections
            SET name = %(name)s, base_url = %(url)s, auth_type = %(auth)s,
                network_scope = %(scope)s, enabled = %(enabled)s,
                status = %(status)s, updated_by = %(actor)s, updated_at = now()
            WHERE id = %(cid)s
            """,
            {"name": body.name or row["name"], "url": next_url,
             "auth": body.auth_type or row["auth_type"], "scope": next_scope,
             "enabled": enabled, "status": status,
             "actor": actor, "cid": conn_id})
        if body.secret is not None:
            if body.secret:
                _upsert_credential(conn_id, body.secret, actor)
            else:
                execute_fn(
                    "DELETE FROM ai.mcp_connection_credentials "
                    "WHERE connection_id = %(cid)s", {"cid": conn_id})
        audit_fn("mcp_connection_update", {
            "connection_id": conn_id,
            "fields": [k for k, v in body.model_dump(exclude={"secret"}).items()
                       if v is not None],
            "credential_changed": body.secret is not None,
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "updated": True}

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator, "mcp_connection_update",
        {"connection_id": conn_id}, _write)
    await _refresh_runtime()
    return result


@router.delete("/connections/{conn_id}")
async def delete_connection(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    row = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    def _write() -> dict[str, Any]:
        query_one = current_query_one(_query_one)
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        refs = query_one(
            """
            SELECT count(*) AS tools,
                   count(*) FILTER (WHERE status = 'approved') AS approved
            FROM ai.mcp_tools WHERE connection_id = %(cid)s
            """, {"cid": conn_id})
        grants = query_one(
            """
            SELECT count(*) AS n FROM ai.mcp_tool_grants g
            JOIN ai.mcp_tools t ON t.id = g.tool_id
            WHERE t.connection_id = %(cid)s AND g.enabled
            """, {"cid": conn_id})
        if (refs and refs["tools"]) or (grants and grants["n"]):
            raise _MutationConflict(
                f"连接仍有 {refs['tools']} 个工具记录"
                f"（{refs['approved']} 个已审核）与 {grants['n']} 条有效授权，"
                "请先停用并清理工具与授权")
        execute_fn("DELETE FROM ai.mcp_connections WHERE id = %(cid)s",
                   {"cid": conn_id})
        audit_fn("mcp_connection_delete", {"connection_id": conn_id},
                 user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "deleted": True}

    try:
        result = await asyncio.to_thread(
            current_run_governance_mutation(run_governance_mutation), request, operator,
            "mcp_connection_delete", {"connection_id": conn_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/test")
async def test_connection(request: Request, conn_id: str):
    """连接测试：与发现/调用是不同操作；连接成功不代表任何工具获得执行权限。

    - streamable_http：MCP initialize 握手 + tools/list；
    - http_api：对 base_url 发起一次 HEAD 探活（404/405 也算可达——
      探活验证的是网络与 TLS 可达性，不要求根路径有路由）。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    audit_fn = current_audit(_audit)

    row = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")
    try:
        _validate_connection_url(row["base_url"], row["network_scope"])
    except ValueError as exc:
        return _bad_request(str(exc))

    secret = ""
    if row["auth_type"] == "static_bearer" and row.get("secret_fingerprint"):
        secret = await asyncio.to_thread(_connection_secret, conn_id)
        if not secret:
            return _bad_request("凭据解密失败，请重新配置", code="SECRET_ERROR")
    headers = _conn_headers(row["auth_type"], secret)
    started = time.perf_counter()

    if row.get("transport") == "http_api":
        probe_fn = current_probe_http_api(_probe_http_api)
        elapsed_ms, ok, error = await asyncio.to_thread(
            probe_fn, row["base_url"], headers)
        if ok:
            await asyncio.to_thread(_record_probe_result, conn_id, tenant,
                                    True, "", elapsed_ms, "manual")
            await _refresh_runtime()
            audit_fn("mcp_connection_test", {
                "connection_id": conn_id, "ok": True,
                "elapsed_ms": elapsed_ms, "transport": "http_api",
            }, user_id=actor, tenant_id=tenant)
            return {"ok": True, "elapsed_ms": elapsed_ms,
                    "transport": "http_api"}
        await asyncio.to_thread(_record_probe_result, conn_id, tenant,
                                False, error, elapsed_ms, "manual")
        audit_fn("mcp_connection_test", {
            "connection_id": conn_id, "ok": False,
            "elapsed_ms": elapsed_ms, "transport": "http_api",
            "error_class": "connection_failed",
        }, user_id=actor, tenant_id=tenant)
        return {"ok": False, "elapsed_ms": elapsed_ms, "transport": "http_api",
                "error_class": "connection_failed", "error": error[:500]}

    try:
        tools = await asyncio.to_thread(
            mcp_list_tools, row["base_url"], timeout=15.0, headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await asyncio.to_thread(_record_probe_result, conn_id, tenant,
                                True, "", elapsed_ms, "manual")
        await _refresh_runtime()
        audit_fn("mcp_connection_test", {
            "connection_id": conn_id, "ok": True, "elapsed_ms": elapsed_ms,
            "tool_count": len(tools),
        }, user_id=actor, tenant_id=tenant)
        return {
            "ok": True, "elapsed_ms": elapsed_ms, "tool_count": len(tools),
            "tool_names": [t["name"] for t in tools][:50],
        }
    except Exception as exc:  # noqa: BLE001 — 测试端点收口全部异常
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        error_class = _classify_mcp_error(exc)
        await asyncio.to_thread(_record_probe_result, conn_id, tenant,
                                False, str(exc), elapsed_ms, "manual")
        audit_fn("mcp_connection_test", {
            "connection_id": conn_id, "ok": False, "elapsed_ms": elapsed_ms,
            "error_class": error_class,
        }, user_id=actor, tenant_id=tenant)
        return {
            "ok": False, "elapsed_ms": elapsed_ms,
            "error_class": error_class, "error": str(exc)[:500],
        }


@router.post("/connections/{conn_id}/tools/manual")
async def register_http_tool(request: Request, conn_id: str,
                             body: HttpToolRegisterRequest):
    """手工登记 HTTP API 工具（仅 http_api 连接；MCP 连接走发现）。

    登记即产生 pending 工具（不可执行）；invocation 经受控校验并计入
    content_hash，审核通过后才进入运行时快照。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    conn = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if conn is None:
        return _not_found("连接不存在")
    if conn["transport"] != "http_api":
        return _bad_request(
            "手工登记仅支持 HTTP API 连接；MCP 连接请使用发现",
            code="TRANSPORT_MISMATCH")
    try:
        input_schema = spec_lib.normalize_input_schema(body.input_schema)
        invocation = spec_lib.validate_invocation(body.invocation, input_schema)
    except spec_lib.SpecDeriveError as exc:
        return _bad_request(f"调用定义非法: {exc}")

    slug = spec_lib.capability_slug(conn["name"], body.tool_name)

    def _write() -> dict[str, Any]:
        audit_fn = current_audit(_audit)
        with _db() as db:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM ai.mcp_tools WHERE capability_slug = %(slug)s",
                    {"slug": slug})
                if cur.fetchone():
                    raise _MutationConflict(f"capability 已存在: mcp.{slug}")
                cur.execute(
                    """
                    INSERT INTO ai.mcp_tools
                        (connection_id, tool_name, capability_slug,
                         description, status, content_hash, invocation,
                         last_seen_version, tenant_id)
                    VALUES (%(cid)s, %(tool)s, %(slug)s, %(desc)s,
                            'pending', %(digest)s, %(inv)s, 1, %(tenant)s)
                    RETURNING id
                    """,
                    {"cid": conn_id, "tool": body.tool_name, "slug": slug,
                     "desc": body.description[:spec_lib.MAX_DESCRIPTION_CHARS],
                     "digest": spec_lib.content_hash(
                         body.description, input_schema, invocation=invocation),
                     "inv": PgJson(invocation), "tenant": tenant})
                tool_row_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO ai.mcp_tool_snapshots
                        (tool_id, discovery_version, content_hash,
                         input_schema, description, invocation)
                    VALUES (%(tid)s, 1, %(digest)s, %(schema)s, %(desc)s,
                            %(inv)s)
                    """,
                    {"tid": tool_row_id, "digest": spec_lib.content_hash(
                        body.description, input_schema, invocation=invocation),
                     "schema": PgJson(input_schema),
                     "desc": body.description,
                     "inv": PgJson(invocation)})
        audit_fn("mcp_http_tool_register", {
            "connection_id": conn_id, "tool_id": tool_row_id,
            "tool_name": body.tool_name, "capability": f"mcp.{slug}",
            "method": invocation.get("method"), "path": invocation.get("path"),
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_row_id, "capability": f"mcp.{slug}",
                "status": "pending"}

    try:
        result = await asyncio.to_thread(
            current_run_governance_mutation(run_governance_mutation), request, operator,
            "mcp_http_tool_register",
            {"connection_id": conn_id, "tool_name": body.tool_name}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.put("/tools/{tool_id}/invocation")
async def update_http_tool_invocation(request: Request, tool_id: int,
                                      body: HttpToolInvocationUpdateRequest):
    """编辑 HTTP API 工具调用定义：产生新快照版本。

    pending 工具直接更新待审内容；approved 工具置 schema_changed=true
    （漂移，立即退出运行时，重审后恢复）——调用目标变化等同契约变化。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row.get("conn_transport") != "http_api":
        return _bad_request(
            "仅 HTTP API 工具支持编辑调用定义", code="TRANSPORT_MISMATCH")
    if row["status"] == "retired":
        return _conflict("上游已消失（retired）的工具不能编辑")
    description = body.description or row["description"]
    try:
        input_schema = spec_lib.normalize_input_schema(body.input_schema)
        invocation = spec_lib.validate_invocation(body.invocation, input_schema)
    except spec_lib.SpecDeriveError as exc:
        return _bad_request(f"调用定义非法: {exc}")
    digest = spec_lib.content_hash(description, input_schema, invocation=invocation)
    next_version = int(row["last_seen_version"] or 0) + 1

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        execute_fn(
            """
            UPDATE ai.mcp_tools
            SET description = %(desc)s, invocation = %(inv)s,
                content_hash = %(digest)s,
                schema_changed = (status = 'approved'),
                last_seen_version = %(version)s, updated_at = now()
            WHERE id = %(tid)s
            """,
            {"desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
             "inv": PgJson(invocation), "digest": digest,
             "version": next_version, "tid": tool_id})
        execute_fn(
            """
            INSERT INTO ai.mcp_tool_snapshots
                (tool_id, discovery_version, content_hash, input_schema,
                 description, invocation)
            VALUES (%(tid)s, %(version)s, %(digest)s, %(schema)s, %(desc)s,
                    %(inv)s)
            """,
            {"tid": tool_id, "version": next_version, "digest": digest,
             "schema": PgJson(input_schema),
             "desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
             "inv": PgJson(invocation)})
        audit_fn("mcp_http_tool_invocation_update", {
            "tool_id": tool_id, "capability": f"mcp.{row['capability_slug']}",
            "version": next_version,
            "drift": row["status"] == "approved",
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_id, "content_hash": digest,
                "version": next_version,
                "schema_changed": row["status"] == "approved"}

    try:
        result = await asyncio.to_thread(
            current_run_governance_mutation(run_governance_mutation), request, operator,
            "mcp_http_tool_invocation_update",
            {"tool_id": tool_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/discover")
async def discover_tools(request: Request, conn_id: str):
    """工具发现：tools/list → 快照入库 → 漂移检测。

    新工具默认 pending（不可执行）；上游内容变化使 approved 工具进入
    待重审；上游消失的工具转 retired。发现成功不代表可执行。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    row = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")
    try:
        _validate_connection_url(row["base_url"], row["network_scope"])
    except ValueError as exc:
        return _bad_request(str(exc))

    secret = ""
    if row["auth_type"] == "static_bearer" and row.get("secret_fingerprint"):
        secret = await asyncio.to_thread(_connection_secret, conn_id)
        if not secret:
            return _bad_request("凭据解密失败，请重新配置", code="SECRET_ERROR")
    headers = _conn_headers(row["auth_type"], secret)

    def _discover() -> dict[str, Any]:
        audit_fn = current_audit(_audit)
        started = time.perf_counter()
        try:
            upstream = mcp_list_tools(row["base_url"], timeout=20.0,
                                      headers=headers)
        except Exception as exc:  # noqa: BLE001 — 上游失败必须显式返回
            _mark_connection_status(conn_id, "error", str(exc))
            audit_fn("mcp_connection_discover", {
                "connection_id": conn_id, "ok": False,
                "error_class": _classify_mcp_error(exc),
            }, user_id=actor, tenant_id=tenant)
            return {
                "ok": False, "error_class": _classify_mcp_error(exc),
                "error": str(exc)[:500],
            }

        with _db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                version = int(row["discovery_version"]) + 1
                cur.execute(
                    "SELECT id, tool_name, content_hash, status, "
                    "capability_slug FROM ai.mcp_tools "
                    "WHERE connection_id = %(cid)s", {"cid": conn_id})
                existing = {r["tool_name"]: dict(r) for r in cur.fetchall()}
                result: dict[str, list] = {
                    "new": [], "changed": [], "unchanged": [],
                    "skipped": [], "retired": [],
                }
                seen_names = set()
                for item in upstream:
                    name = str(item.get("name") or "").strip()
                    if not name:
                        result["skipped"].append(
                            {"tool": "", "reason": "上游工具名为空"})
                        continue
                    seen_names.add(name)
                    description = str(item.get("description") or "")
                    input_schema = item.get("input_schema") or {}
                    output_schema = item.get("output_schema")
                    digest = spec_lib.content_hash(
                        description, input_schema, output_schema)
                    prev = existing.get(name)
                    if prev is None:
                        slug = spec_lib.capability_slug(row["name"], name)
                        cur.execute(
                            "SELECT 1 FROM ai.mcp_tools "
                            "WHERE capability_slug = %(slug)s", {"slug": slug})
                        if cur.fetchone():
                            result["skipped"].append({
                                "tool": name,
                                "reason": f"capability slug 冲突: {slug}"})
                            continue
                        cur.execute(
                            """
                            INSERT INTO ai.mcp_tools
                                (connection_id, tool_name, capability_slug,
                                 description, status, content_hash,
                                 last_seen_version, tenant_id)
                            VALUES (%(cid)s, %(tool)s, %(slug)s, %(desc)s,
                                    'pending', %(digest)s, %(version)s,
                                    %(tenant)s)
                            RETURNING id
                            """,
                            {"cid": conn_id, "tool": name, "slug": slug,
                             "desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
                             "digest": digest, "version": version,
                             "tenant": tenant})
                        tool_row_id = cur.fetchone()["id"]
                        cur.execute(
                            """
                            INSERT INTO ai.mcp_tool_snapshots
                                (tool_id, discovery_version, content_hash,
                                 input_schema, output_schema, description)
                            VALUES (%(tid)s, %(version)s, %(digest)s,
                                    %(schema)s, %(oschema)s, %(desc)s)
                            """,
                            {"tid": tool_row_id, "version": version,
                             "digest": digest,
                             "schema": PgJson(input_schema),
                             "oschema": PgJson(output_schema)
                             if output_schema is not None else None,
                             "desc": description})
                        result["new"].append(
                            {"tool": name, "capability": f"mcp.{slug}"})
                        continue
                    # 已有工具：漂移检测
                    cur.execute(
                        "UPDATE ai.mcp_tools SET last_seen_version = %(v)s, "
                        "updated_at = now() WHERE id = %(tid)s",
                        {"v": version, "tid": prev["id"]})
                    if digest == prev["content_hash"]:
                        result["unchanged"].append({"tool": name})
                        continue
                    cur.execute(
                        """
                        INSERT INTO ai.mcp_tool_snapshots
                            (tool_id, discovery_version, content_hash,
                             input_schema, output_schema, description)
                        VALUES (%(tid)s, %(version)s, %(digest)s,
                                %(schema)s, %(oschema)s, %(desc)s)
                        """,
                        {"tid": prev["id"], "version": version,
                         "digest": digest, "schema": PgJson(input_schema),
                         "oschema": PgJson(output_schema)
                         if output_schema is not None else None,
                         "desc": description})
                    if prev["status"] == "approved":
                        # 上游内容变化：待重审（approved_hash 失配即运行时失效）
                        cur.execute(
                            "UPDATE ai.mcp_tools SET content_hash = %(d)s, "
                            "schema_changed = true, updated_at = now() "
                            "WHERE id = %(tid)s",
                            {"d": digest, "tid": prev["id"]})
                        result["changed"].append({
                            "tool": name,
                            "reason": "schema_changed_rereview_required"})
                    else:
                        cur.execute(
                            "UPDATE ai.mcp_tools SET content_hash = %(d)s, "
                            "updated_at = now() WHERE id = %(tid)s",
                            {"d": digest, "tid": prev["id"]})
                        result["changed"].append(
                            {"tool": name, "reason": "pending_updated"})
                # 上游消失的工具 → retired（approved 的立即退出运行时）
                for name, prev in existing.items():
                    if name in seen_names or prev["status"] == "retired":
                        continue
                    cur.execute(
                        "UPDATE ai.mcp_tools SET status = 'retired', "
                        "retired_reason = 'upstream_missing', "
                        "updated_at = now() WHERE id = %(tid)s",
                        {"tid": prev["id"]})
                    result["retired"].append({"tool": name})

                cur.execute(
                    "UPDATE ai.mcp_connections SET status = 'connected', "
                    "last_synced_at = now(), discovery_version = %(v)s, "
                    "last_error = '', updated_at = now() WHERE id = %(cid)s",
                    {"v": version, "cid": conn_id})
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        audit_fn("mcp_connection_discover", {
            "connection_id": conn_id, "ok": True, "elapsed_ms": elapsed_ms,
            "new": len(result["new"]), "changed": len(result["changed"]),
            "retired": len(result["retired"]), "version": version,
        }, user_id=actor, tenant_id=tenant)
        return {"ok": True, "elapsed_ms": elapsed_ms,
                "discovery_version": version, **result}

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator,
        "mcp_connection_discover", {"connection_id": conn_id}, _discover)
    await _refresh_runtime()
    return result


async def _set_connection_enabled(request: Request, conn_id: str,
                                  enabled: bool):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_connection_fn = current_load_connection(_load_connection)
    row = await asyncio.to_thread(load_connection_fn, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        execute_fn(
            "UPDATE ai.mcp_connections SET enabled = %(enabled)s, "
            "status = %(status)s, updated_by = %(actor)s, updated_at = now() "
            "WHERE id = %(cid)s",
            {"enabled": enabled,
             "status": "connected" if enabled else "disabled",
             "actor": actor, "cid": conn_id})
        audit_fn("mcp_connection_set_enabled", {
            "connection_id": conn_id, "enabled": enabled,
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "enabled": enabled}

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator,
        "mcp_connection_set_enabled",
        {"connection_id": conn_id, "enabled": enabled}, _write)
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/enable")
async def enable_connection(request: Request, conn_id: str):
    return await _set_connection_enabled(request, conn_id, True)


@router.post("/connections/{conn_id}/disable")
async def disable_connection(request: Request, conn_id: str):
    return await _set_connection_enabled(request, conn_id, False)
