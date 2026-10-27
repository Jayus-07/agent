"""health.py — 连接健康巡检（Batch 5-3，决策 #16）

非副作用探活 + 断线退避重连 + 健康事件留痕。探活语义与管理端 `/test`
完全同口径（probe.py 规范实现）；探活成功即恢复 connected。

诚实边界（迁移 118 注释同款）：
  - **不动 enabled 管理开关**——重连 ≠ 重新启用；
  - 不自动发现——契约漂移隔离仍走「发现 → schema_changed → 执行拒绝
    → 重新审核」既有链；
  - 不改执行门——巡检是可观测与自愈节奏，调用授权/熔断由 Governance 管。

节奏：每 MCP_HEALTH_SCAN_INTERVAL_S（默认 60s）扫一次到期的启用连接；
单连接探活周期 = MCP_HEALTH_PROBE_INTERVAL_S（默认 300s）×
2^min(probe_fail_count, 4)（退避上限 ×16）。事件表 7 天自动裁剪。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from backend.shared.logger import logger

# 单连接基础探活周期（秒）与扫描间隔
PROBE_INTERVAL_S = float(os.getenv("MCP_HEALTH_PROBE_INTERVAL_S", "300"))
SCAN_INTERVAL_S = float(os.getenv("MCP_HEALTH_SCAN_INTERVAL_S", "60"))
# 退避指数上限（×2^4 = 16 倍基础周期）
_BACKOFF_MAX_SHIFT = 4
# 事件保留天数
_EVENT_RETENTION_DAYS = 7


def backoff_seconds(fail_count: int) -> float:
    """连续失败 N 次后的探活周期（基础 × 2^min(N,4)，上限 ×16）。"""
    return PROBE_INTERVAL_S * float(2 ** min(max(int(fail_count), 0), _BACKOFF_MAX_SHIFT))


def _query_all(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    from backend.app.api.routes.admin_mcp import _query_all as _impl

    return _impl(sql, params)


def _execute(sql: str, params: dict | None = None) -> int:
    from backend.app.api.routes.admin_mcp import _execute as _impl

    return _impl(sql, params)


def _load_enabled_connections() -> list[dict[str, Any]]:
    return _query_all(
        "SELECT id, name, base_url, transport, auth_type, network_scope, "
        "       status, probe_fail_count, "
        "       EXTRACT(EPOCH FROM (COALESCE(last_checked_at, "
        "         '1970-01-01'::timestamptz))) AS last_checked_epoch "
        "FROM ai.mcp_connections WHERE enabled ORDER BY name")


def _connection_secret(conn_id: str) -> str:
    from backend.app.api.routes.admin_mcp import _connection_secret as _impl

    return _impl(conn_id)


def probe_once(conn: dict[str, Any], *, source: str = "monitor") -> dict[str, Any]:
    """探活单个连接并落库（状态/失败计数/事件）。线程内执行，返回事件摘要。

    URL 二次校验沿用 SSRF 红线：校验失败的连接记事件并跳过出网（不重试）。
    """
    from backend.infra.mcp_registry.probe import (
        conn_headers, probe_http_api, probe_mcp_tools,
    )
    from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

    conn_id = conn["id"]
    try:
        assert_url_allowed(
            conn["base_url"], allow_private=(conn["network_scope"] == "private"))
    except UrlBlockedError as exc:
        result = {"ok": False, "elapsed_ms": 0,
                  "error": f"URL 校验失败: {exc}", "tool_count": 0}
        _record(conn, result, source)
        return result

    secret = ""
    if conn["auth_type"] == "static_bearer":
        secret = _connection_secret(conn_id)
    headers = conn_headers(conn["auth_type"], secret)

    if conn["transport"] == "http_api":
        elapsed, ok, error = probe_http_api(conn["base_url"], headers)
        result = {"ok": ok, "elapsed_ms": elapsed, "error": error, "tool_count": 0}
    else:
        elapsed, ok, error, tool_count = probe_mcp_tools(
            conn["base_url"], 15.0, headers)
        result = {"ok": ok, "elapsed_ms": elapsed, "error": error,
                  "tool_count": tool_count}
    _record(conn, result, source)
    return result


def _record(conn: dict[str, Any], result: dict[str, Any], source: str) -> None:
    """落库：状态 + 失败计数（退避依据）+ 健康事件 + 运行时快照对齐。"""
    ok = bool(result["ok"])
    fail_count = 0 if ok else int(conn.get("probe_fail_count") or 0) + 1
    status = "connected" if ok else "error"
    _execute(
        "UPDATE ai.mcp_connections SET status = %(status)s, "
        "last_error = %(err)s, last_checked_at = now(), "
        "probe_fail_count = %(fails)s, updated_at = now() WHERE id = %(cid)s",
        {"status": status, "err": str(result["error"])[:1000],
         "fails": fail_count, "cid": conn["id"]})
    _execute(
        "INSERT INTO ai.mcp_health_events "
        "(connection_id, tenant_id, ok, error, elapsed_ms, source) "
        "SELECT id, tenant_id, %(ok)s, %(err)s, %(ms)s, %(src)s "
        "FROM ai.mcp_connections WHERE id = %(cid)s",
        {"ok": ok, "err": str(result["error"])[:1000],
         "ms": int(result["elapsed_ms"]), "src": source, "cid": conn["id"]})
    logger.info(
        "[HealthMonitor] %s 连接 %s: %s (%dms, fail_count=%d)",
        source, conn.get("name"), "正常" if ok else "异常",
        result["elapsed_ms"], fail_count)


def prune_events() -> int:
    """裁剪保留期外的事件（巡检循环低频调用）。返回删除行数。"""
    return _execute(
        "DELETE FROM ai.mcp_health_events WHERE created_at < now() "
        "- make_interval(days => %(days)s)",
        {"days": _EVENT_RETENTION_DAYS})


def _is_due(conn: dict[str, Any], now_epoch: float) -> bool:
    last = float(conn.get("last_checked_epoch") or 0)
    return (now_epoch - last) >= backoff_seconds(
        int(conn.get("probe_fail_count") or 0))


async def scan_once() -> dict[str, int]:
    """扫一轮到期连接（探活在线程池顺序执行，避免并发打爆弱上游）。"""
    conns = await asyncio.to_thread(_load_enabled_connections)
    now_epoch = time.time()
    due = [c for c in conns if _is_due(c, now_epoch)]
    ok_count = 0
    for conn in due:
        try:
            result = await asyncio.to_thread(probe_once, conn)
            ok_count += 1 if result["ok"] else 0
        except Exception:  # noqa: BLE001 — 单连接失败不中断巡检
            logger.warning("[HealthMonitor] 探活异常: %s",
                           conn.get("name"), exc_info=True)
    if due:
        try:
            from backend.infra.mcp_registry import store as mcp_store

            await mcp_store.refresh()
        except Exception:  # noqa: BLE001 — 快照刷新由轮询兜底
            logger.debug("[HealthMonitor] 快照刷新失败（轮询兜底）", exc_info=True)
    return {"probed": len(due), "ok": ok_count}


async def health_loop() -> None:
    """后台巡检主循环（server startup 挂载；异常不退出）。"""
    logger.info("[HealthMonitor] 启动：scan=%.0fs probe=%.0fs",
                SCAN_INTERVAL_S, PROBE_INTERVAL_S)
    cycles = 0
    while True:
        try:
            await scan_once()
        except Exception:  # noqa: BLE001 — 巡检循环永不退出
            logger.warning("[HealthMonitor] 扫描轮异常，保留上次状态",
                           exc_info=True)
        cycles += 1
        if cycles % 60 == 0:  # 约每小时裁剪一次过期事件
            try:
                await asyncio.to_thread(prune_events)
            except Exception:  # noqa: BLE001
                logger.debug("[HealthMonitor] 事件裁剪失败", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL_S)


def enabled_by_env() -> bool:
    return os.getenv("MCP_HEALTH_MONITOR", "1").strip().lower() in (
        "1", "true", "yes", "on")


__all__ = [
    "backoff_seconds", "enabled_by_env", "health_loop", "prune_events",
    "probe_once", "scan_once",
]
