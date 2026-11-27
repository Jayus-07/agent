"""admin_mcp_subroutes/overview_routes.py — MCP 概览、健康监测与配置导出路由。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request

from backend.app.api.deps import require_admin_user
from backend.app.api.routes.admin_mcp_subroutes.common import (
    _actor_of,
    _audit,
    _export_payload,
    _query_all,
    _tenant_of,
)
from backend.app.api.routes.admin_mcp_subroutes.dynamic import (
    current_audit,
    current_export_payload,
    current_query_all,
)

from backend.app.api.routes.admin_mcp_subroutes.routes import router


@router.get("/overview")
async def mcp_overview(request: Request):
    await require_admin_user(request)
    from backend.infra.mcp_registry import store as mcp_store

    snap = mcp_store.snapshot()
    return {
        "loaded": snap.loaded,
        "active_tools": sorted(snap.tools.keys()),
        "connection_count": len(snap.connections),
        "grant_count": len(snap.grants),
        "derive_errors": [{"capability": cap, "reason": reason}
                          for cap, reason in snap.errors],
        "refreshed_at": snap.refreshed_at,
    }


@router.get("/health")
async def health_overview(request: Request):
    """连接健康总览（决策 #16，Batch 5-3）：巡检状态 + 退避 + 最近事件。

    只读聚合；探活节奏见 infra/mcp_registry/health.py（基础周期 ×
    2^min(失败次数,4)，上限 ×16）。影响工具按连接从 /tools 列表过滤呈现。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> dict[str, Any]:
        from backend.infra.mcp_registry.health import backoff_seconds

        query_all = current_query_all(_query_all)
        conns = query_all(
            "SELECT c.id, c.name, c.transport, c.status, c.network_scope, "
            "       c.enabled, c.last_error, c.last_checked_at, "
            "       c.probe_fail_count, "
            "       (SELECT count(*) FROM ai.mcp_tools t "
            "         WHERE t.connection_id = c.id) AS tool_count, "
            "       (SELECT count(*) FROM ai.mcp_tools t "
            "         WHERE t.connection_id = c.id AND t.status = 'approved') "
            "         AS approved_count "
            "FROM ai.mcp_connections c "
            "WHERE c.enabled = true AND c.tenant_id = %(tenant)s "
            "ORDER BY c.name", {"tenant": tenant})
        conn_ids = [str(c["id"]) for c in conns]
        events_by_conn: dict[str, list[dict[str, Any]]] = {}
        if conn_ids:
            for e in query_all(
                    "SELECT * FROM ("
                    "  SELECT e.id, e.connection_id, e.ok, e.error, "
                    "         e.elapsed_ms, e.source, e.created_at, "
                    "         row_number() OVER (PARTITION BY e.connection_id "
                    "           ORDER BY e.created_at DESC) AS rn "
                    "  FROM ai.mcp_health_events e "
                    "  WHERE e.connection_id = ANY(%(ids)s)"
                    ") t WHERE t.rn <= 5 "
                    "ORDER BY t.connection_id, t.created_at DESC",
                    {"ids": conn_ids}):
                events_by_conn.setdefault(str(e["connection_id"]), []).append({
                    "ok": e["ok"], "error": e["error"],
                    "elapsed_ms": e["elapsed_ms"], "source": e["source"],
                    "created_at": e["created_at"].isoformat(),
                })
        views = []
        for c in conns:
            backoff = backoff_seconds(int(c["probe_fail_count"] or 0))
            last_epoch = (
                c["last_checked_at"].timestamp() if c["last_checked_at"] else 0)
            views.append({
                "id": c["id"], "name": c["name"], "transport": c["transport"],
                "status": c["status"], "network_scope": c["network_scope"],
                "last_error": c["last_error"] or "",
                "last_checked_at": (
                    c["last_checked_at"].isoformat()
                    if c["last_checked_at"] else None),
                "probe_fail_count": int(c["probe_fail_count"] or 0),
                "probe_interval_seconds": int(backoff),
                "next_probe_in_seconds": max(
                    0, int(backoff - (time.time() - last_epoch))
                ) if last_epoch else 0,
                "tool_count": int(c["tool_count"]),
                "approved_count": int(c["approved_count"]),
                "recent_events": events_by_conn.get(str(c["id"]), []),
            })
        return {"connections": views, "probe_interval_seconds": 300}

    return await asyncio.to_thread(_load)


@router.get("/export")
async def export_configuration(request: Request):
    """配置导出（Batch 4，只读）：连接声明 + 能力包 + 授权关系，目标环境
    重新映射与审核的输入。

    红线（ADR-0011 D7）：
      - 凭据密文、fingerprint、last4 **绝不导出**——目标环境重新配置；
      - URL 内嵌凭据（user:pass@host / ?api_key= / #fragment）导出前净化，
        无法安全净化的连接拒绝导出（见 redacted/rejected_connections）；
      - 工具快照/审核状态/审批历史不导出——审核必须在新环境重新进行；
      - 导出内容含授权关系（bundle→product），由导入方管理员确认后重建。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)

    def _load() -> dict[str, Any]:
        export_payload_fn = current_export_payload(_export_payload)
        audit_fn = current_audit(_audit)
        payload = export_payload_fn(tenant)
        audit_fn("mcp_configuration_export", {
            "connections": len(payload["connections"]),
            "bundles": len(payload["capability_bundles"]),
            "redacted": len(payload["redacted_connections"]),
            "rejected": len(payload["rejected_connections"]),
        }, user_id=actor, tenant_id=tenant)
        return {
            "version": 1,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tenant": tenant,
            "connections": payload["connections"],
            "capability_bundles": payload["capability_bundles"],
            "redacted_connections": payload["redacted_connections"],
            "rejected_connections": payload["rejected_connections"],
        }

    return await asyncio.to_thread(_load)
