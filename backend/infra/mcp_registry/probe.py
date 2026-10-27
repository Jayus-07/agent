"""probe.py — 连接探活原语（Batch 5-3，决策 #16）

管理端 `/test` 与后台健康巡检共用的非副作用探活实现：
  - http_api：HEAD base_url，验证网络/TLS 可达性（任何 HTTP 响应含
    404/405 都算可达——根路径不要求有路由）；
  - streamable_http：MCP initialize 握手 + tools/list（只读语义）。

admin_mcp._probe_http_api / _conn_headers 已改为本模块的委托别名；
健康巡检（health.py）直接消费本模块，不反向依赖路由层。
"""
from __future__ import annotations

import time
from typing import Any

from backend.shared.logger import logger


def conn_headers(auth_type: str, secret: str) -> dict[str, str] | None:
    """连接出网鉴权头（static_bearer → Authorization；其余无头）。"""
    if auth_type == "static_bearer" and secret:
        return {"Authorization": f"Bearer {secret}"}
    return None


def probe_http_api(
    base_url: str, headers: dict[str, str] | None,
) -> tuple[int, bool, str]:
    """HTTP API 连接探活：HEAD base_url。返回 (elapsed_ms, ok, error)。"""
    import httpx

    started = time.perf_counter()
    try:
        with httpx.Client(trust_env=False, timeout=10.0) as client:
            response = client.head(str(base_url or ""), headers=headers or None,
                                   follow_redirects=True)
        elapsed = int((time.perf_counter() - started) * 1000)
        return elapsed, True, ""
    except Exception as exc:  # noqa: BLE001 — 探活收口全部网络异常
        elapsed = int((time.perf_counter() - started) * 1000)
        return elapsed, False, f"{type(exc).__name__}: {exc}"


def probe_mcp_tools(
    base_url: str, timeout: float, headers: dict[str, str] | None,
) -> tuple[int, bool, str, int]:
    """MCP 连接探活：握手 + tools/list（只读）。返回 (elapsed_ms, ok, error, tool_count)。"""
    from backend.infra.mcp_client import list_tools as mcp_list_tools

    started = time.perf_counter()
    try:
        tools: list[dict[str, Any]] = mcp_list_tools(
            base_url, timeout=timeout, headers=headers)
        elapsed = int((time.perf_counter() - started) * 1000)
        return elapsed, True, "", len(tools)
    except Exception as exc:  # noqa: BLE001 — 探活收口全部异常
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.debug("[Probe] MCP 探活失败: %s", exc)
        return elapsed, False, f"{type(exc).__name__}: {exc}", 0


__all__ = ["conn_headers", "probe_http_api", "probe_mcp_tools"]
