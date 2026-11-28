"""admin_mcp_subroutes/tool_routes.py — MCP 工具查询、审核、授权与测试调用路由。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from psycopg2.extras import Json as PgJson

from backend.app.api.deps import require_admin_user, run_governance_mutation
from backend.app.api.routes.admin_mcp_subroutes.common import (
    _TOOL_SELECT,
    _actor_of,
    _audit,
    _bad_request,
    _conflict,
    _current_input_schema,
    _execute,
    _load_connection,
    _load_tool,
    _not_found,
    _query_all,
    _refresh_runtime,
    _tenant_of,
    _tool_list_view,
    _tool_view,
)
from backend.app.api.routes.admin_mcp_subroutes.dynamic import (
    current_audit,
    current_current_input_schema,
    current_execute,
    current_load_connection,
    current_load_tool,
    current_query_all,
    current_tool_list_view,
    current_tool_view,
    current_run_governance_mutation,
)
from backend.app.api.routes.admin_mcp_subroutes.models import (
    GrantRequest,
    ReviewRequest,
    TestInvokeRequest,
    _MutationConflict,
)

from backend.app.api.routes.admin_mcp_subroutes.routes import router


@router.get("/connections/{conn_id}/tools")
async def list_connection_tools(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> dict[str, Any]:
        load_connection_fn = current_load_connection(_load_connection)
        query_all = current_query_all(_query_all)
        tool_list_view_fn = current_tool_list_view(_tool_list_view)

        conn = load_connection_fn(conn_id, tenant)
        if conn is None:
            return {}
        rows = query_all(
            f"{_TOOL_SELECT} WHERE t.connection_id = %(cid)s "
            "AND t.tenant_id = %(tenant)s ORDER BY t.tool_name",
            {"cid": conn_id, "tenant": tenant})
        grants = query_all(
            "SELECT tool_id, product_key, granted_by, granted_at "
            "FROM ai.mcp_tool_grants "
            "WHERE tenant_id = %(tenant)s AND enabled", {"tenant": tenant})
        grants_by_tool: dict[int, list[dict]] = {}
        for g in grants:
            grants_by_tool.setdefault(int(g["tool_id"]), []).append({
                "product_key": g["product_key"],
                "granted_by": g["granted_by"],
                "granted_at": g["granted_at"].isoformat(),
            })
        tools = []
        for r in rows:
            view = tool_list_view_fn(r)
            view["grants"] = grants_by_tool.get(int(r["id"]), [])
            tools.append(view)
        return {"tools": tools}

    data = await asyncio.to_thread(_load)
    if not data:
        return _not_found("连接不存在")
    return data


@router.get("/tools")
async def list_all_tools(request: Request, status_filter: str = ""):
    """统一工具目录的 MCP 段（/tools 页面合并展示数据源）。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> list[dict]:
        query_all = current_query_all(_query_all)
        tool_list_view_fn = current_tool_list_view(_tool_list_view)

        where = "t.tenant_id = %(tenant)s"
        params: dict[str, Any] = {"tenant": tenant}
        if status_filter:
            where += " AND t.status = %(status)s"
            params["status"] = status_filter
        rows = query_all(
            f"{_TOOL_SELECT} WHERE {where} ORDER BY t.updated_at DESC", params)
        # 授权产品随列表下发（/tools 统一目录的授权列；与连接内清单同口径）
        grants = query_all(
            "SELECT tool_id, product_key FROM ai.mcp_tool_grants "
            "WHERE tenant_id = %(tenant)s AND enabled", {"tenant": tenant})
        grants_by_tool: dict[int, list[str]] = {}
        for g in grants:
            grants_by_tool.setdefault(int(g["tool_id"]), []).append(
                str(g["product_key"]))
        views = []
        for r in rows:
            view = tool_list_view_fn(r)
            view["grants"] = [
                {"product_key": p} for p in grants_by_tool.get(int(r["id"]), [])]
            views.append(view)
        return views

    return {"tools": await asyncio.to_thread(_load)}


@router.get("/tools/{tool_id}")
async def get_tool(request: Request, tool_id: int):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> dict | None:
        load_tool_fn = current_load_tool(_load_tool)
        query_all = current_query_all(_query_all)
        tool_view_fn = current_tool_view(_tool_view)

        row = load_tool_fn(tool_id, tenant)
        if row is None:
            return None
        snapshots = query_all(
            """
            SELECT discovery_version, content_hash, input_schema,
                   output_schema, description, discovered_at
            FROM ai.mcp_tool_snapshots WHERE tool_id = %(tid)s
            ORDER BY discovery_version DESC, id DESC LIMIT 20
            """, {"tid": tool_id})
        grants = query_all(
            "SELECT product_key, enabled, granted_by, granted_at, revoked_at "
            "FROM ai.mcp_tool_grants WHERE tool_id = %(tid)s "
            "ORDER BY granted_at DESC", {"tid": tool_id})
        view = tool_view_fn(row)
        view["snapshots"] = [{
            "discovery_version": s["discovery_version"],
            "content_hash": s["content_hash"],
            "input_schema": s["input_schema"],
            "output_schema": s["output_schema"],
            "description": s["description"],
            "discovered_at": s["discovered_at"].isoformat(),
        } for s in snapshots]
        view["grants"] = [{
            "product_key": g["product_key"], "enabled": bool(g["enabled"]),
            "granted_by": g["granted_by"],
            "granted_at": g["granted_at"].isoformat(),
            "revoked_at": g["revoked_at"].isoformat()
            if g["revoked_at"] else None,
        } for g in grants]
        return view

    view = await asyncio.to_thread(_load)
    if view is None:
        return _not_found("工具不存在")
    return view


@router.post("/tools/{tool_id}/review")
async def review_tool(request: Request, tool_id: int, body: ReviewRequest):
    """审核（B2）：approve 把治理配置与路由示例落库并绑定 approved_hash；
    reject 仅记录结论。未审核工具永不进入运行时快照。"""
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    current_input_schema_fn = current_current_input_schema(_current_input_schema)

    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row["status"] == "retired":
        return _conflict("上游已消失（retired）的工具不能审核，请等待重新发现")

    # 审核前本地校验治理配置（fail-fast）
    capability = f"mcp.{row['capability_slug']}"
    if body.action == "approve":
        schema = await asyncio.to_thread(current_input_schema_fn, tool_id)
        try:
            spec_lib.derive_tool_spec(
                capability, body.description or row["description"],
                schema, body.governance)
            spec_lib.derive_capability_decl(
                capability, body.description or row["description"],
                schema, body.governance, body.examples)
            if row.get("conn_transport") == "http_api":
                # HTTP API 工具：审核绑定时刻再校验一次调用定义
                spec_lib.validate_invocation(
                    row.get("invocation") or {}, schema)
        except spec_lib.SpecDeriveError as exc:
            return _bad_request(f"治理配置非法: {exc}")

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        if body.action == "approve":
            description = body.description or row["description"]
            # 审核绑定所见版本（CAS）：读取后若发现流程改写 content_hash
            # （漂移）或工具转 retired，本次审核拒绝——绝不能用旧摘要
            # 覆盖新内容并清掉漂移标记，否则未审核的新 Schema 会进入运行时。
            updated = execute_fn(
                """
                UPDATE ai.mcp_tools
                SET status = 'approved', description = %(desc)s,
                    governance = %(gov)s, examples = %(examples)s,
                    review_notes = %(notes)s, reviewed_by = %(actor)s,
                    reviewed_at = now(),
                    approved_hash = %(digest)s, content_hash = %(digest)s,
                    schema_changed = false, updated_at = now()
                WHERE id = %(tid)s
                  AND content_hash = %(expected)s
                  AND status <> 'retired'
                """,
                {"desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
                 "gov": PgJson(body.governance),
                 "examples": PgJson(body.examples),
                 "notes": body.notes, "actor": actor,
                 "digest": row["content_hash"], "tid": tool_id,
                 "expected": row["content_hash"]})
            if updated == 0:
                raise _MutationConflict(
                    "审核期间发现内容已变化（上游 Schema 漂移或工具已退役），"
                    "请刷新后按最新版本重新审核")
        else:
            execute_fn(
                """
                UPDATE ai.mcp_tools
                SET status = 'rejected', review_notes = %(notes)s,
                    reviewed_by = %(actor)s, reviewed_at = now(),
                    updated_at = now()
                WHERE id = %(tid)s
                """,
                {"notes": body.notes, "actor": actor, "tid": tool_id})
        audit_fn("mcp_tool_review", {
            "tool_id": tool_id, "capability": capability,
            "action": body.action, "reviewed_by": actor,
            "governance": body.governance if body.action == "approve" else {},
            "examples_count": len(body.examples),
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_id, "status": body.action + "d"}

    try:
        result = await asyncio.to_thread(
            current_run_governance_mutation(run_governance_mutation), request, operator, "mcp_tool_review",
            {"tool_id": tool_id, "action": body.action}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/tools/{tool_id}/disable")
async def disable_tool(request: Request, tool_id: int):
    """停用工具：运行时快照立即移除（新调用被 tool_not_registered 拒绝），
    保留审核记录便于恢复。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        execute_fn(
            "UPDATE ai.mcp_tools SET status = 'disabled', updated_at = now() "
            "WHERE id = %(tid)s", {"tid": tool_id})
        audit_fn("mcp_tool_disable", {"tool_id": tool_id}, user_id=actor,
                 tenant_id=tenant)
        return {"tool_id": tool_id, "status": "disabled"}

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator, "mcp_tool_disable",
        {"tool_id": tool_id}, _write)
    await _refresh_runtime()
    return result


@router.post("/tools/{tool_id}/grants")
async def grant_tool(request: Request, tool_id: int, body: GrantRequest):
    """授权 / 撤销：产品级授权（ADR-0002 服务端产品身份）。仅 approved
    工具可授权；撤销后该产品的新调用立即被拒绝。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    if not body.revoke and row["status"] != "approved":
        return _conflict(
            f"工具当前状态为 {row['status']}，只有 approved 工具可授权")

    if not body.revoke:
        # 决策 #15 业务域硬隔离：审核时登记的业务域（governance.domain）
        # 属目标产品禁行域 → 拒绝授权。未分类（domain 为空）不设限，
        # 兼容存量数据；bundle 路径已按 bundle.domain 校验。
        from backend.core.product_context import (
            _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS,
            domain_exclusive_forbidden_roots,
        )

        tool_domain = str((row.get("governance") or {}).get("domain") or "")
        forbidden = domain_exclusive_forbidden_roots(body.product_key)
        if forbidden & _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS.get(tool_domain, frozenset()):
            return _bad_request(
                f"工具业务域为 {tool_domain}，不可授权给 {body.product_key} "
                "产品入口（业务域硬隔离），请改审核业务域或授权对应产品")

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        if body.revoke:
            execute_fn(
                """
                UPDATE ai.mcp_tool_grants
                SET enabled = false, revoked_at = now()
                WHERE tool_id = %(tid)s AND product_key = %(product)s
                  AND tenant_id = %(tenant)s AND enabled
                """,
                {"tid": tool_id, "product": body.product_key,
                 "tenant": tenant})
        else:
            execute_fn(
                """
                INSERT INTO ai.mcp_tool_grants
                    (tool_id, product_key, enabled, granted_by, tenant_id)
                VALUES (%(tid)s, %(product)s, true, %(actor)s, %(tenant)s)
                ON CONFLICT (tool_id, product_key, tenant_id)
                DO UPDATE SET enabled = true, granted_by = %(actor)s,
                              granted_at = now(), revoked_at = NULL
                """,
                {"tid": tool_id, "product": body.product_key,
                 "actor": actor, "tenant": tenant})
        audit_fn("mcp_tool_grant", {
            "tool_id": tool_id, "product_key": body.product_key,
            "revoked": body.revoke,
            "capability": f"mcp.{row['capability_slug']}",
        }, user_id=actor, tenant_id=tenant)
        return {
            "tool_id": tool_id, "product_key": body.product_key,
            "granted": not body.revoke,
        }

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator, "mcp_tool_grant",
        {"tool_id": tool_id, "product_key": body.product_key,
         "revoke": body.revoke}, _write)
    await _refresh_runtime()
    return result


@router.post("/tools/{tool_id}/test-invoke")
async def test_invoke_tool(request: Request, tool_id: int,
                           body: TestInvokeRequest):
    """真实只读调用测试：走与生产完全相同的治理链（Schema/风险/限流/
    熔断/超时/审计），绝不允许写操作裸测。product_key 决定以哪个产品
    身份执行（internal=服务运维身份全放行；agent/customer_service 会
    执行真实授权检查，未授权即被拒——这正是要验证的行为）。"""
    from backend.core.request_context import (
        set_product_context,
        set_tool_tenant_id,
        set_tool_user_id,
    )
    from backend.core.tool_governance.guard import (
        ToolCallRequest,
        governance_runtime,
    )
    from backend.infra.mcp_registry import store as mcp_store
    from backend.skills.mcp_dynamic.skill import _McpDynamicToolAdapter

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    audit_fn = current_audit(_audit)

    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    capability = f"mcp.{row['capability_slug']}"
    runtime = mcp_store.get_runtime(capability)
    if runtime is None:
        return _conflict(
            f"工具未在运行时生效（status={row['status']}, "
            f"schema_changed={row['schema_changed']}, "
            f"连接启用={row['conn_enabled']}），无法测试")
    operation = str((row.get("governance") or {}).get("operation") or "read")
    if operation != "read":
        return _conflict(
            f"测试调用仅限只读工具（当前 operation={operation}）；"
            "有副作用的操作必须走真实业务链路的确认/审批/幂等机制")

    adapter = _McpDynamicToolAdapter(capability)
    request_obj = ToolCallRequest(
        capability=capability,
        arguments=dict(body.arguments or {}),
        user_id=actor or "admin-test",
        tenant_id=tenant,
    )
    set_product_context(body.product_key, "admin_mcp_test")
    set_tool_tenant_id(tenant)
    set_tool_user_id(actor or "admin-test")
    started = time.perf_counter()
    try:
        result = await governance_runtime.execute(
            request_obj,
            lambda: asyncio.to_thread(adapter.invoke, dict(body.arguments or {})),
            tool_name=capability,
            trace_agent="admin-mcp-test",
            trace_capability=capability,
        )
    finally:
        set_product_context("internal", "internal_call")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    audit_fn("mcp_tool_test_invoke", {
        "tool_id": tool_id, "capability": capability,
        "product_key": body.product_key,
        "status": result.status.value, "elapsed_ms": elapsed_ms,
        "error_code": result.error_code,
    }, user_id=actor, tenant_id=tenant)
    return {
        "tool_id": tool_id,
        "capability": capability,
        "product_key": body.product_key,
        "tool_status": result.status.value,
        "error_code": result.error_code or "",
        "error_message": result.error_message or "",
        "elapsed_ms": elapsed_ms,
        "result": result.data,
    }
