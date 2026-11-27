"""admin_mcp_subroutes/dynamic.py — 门面动态穿透代理。

支持外部测试直接 monkeypatch `backend.app.api.routes.admin_mcp.<func>`，
子路由内部通过 current_* 获取当前生效的绑定（优先门面上的 mock/覆盖）。
"""
from __future__ import annotations

import sys
from typing import Any, Callable

_FACADE_MODULE = "backend.app.api.routes.admin_mcp"


def _resolve_facade_attr(name: str, fallback: Callable[..., Any]) -> Callable[..., Any]:
    mod = sys.modules.get(_FACADE_MODULE)
    if mod is not None and hasattr(mod, name):
        val = getattr(mod, name)
        if callable(val):
            return val
    return fallback


def current_db(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_db", fallback)


def current_query_all(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_query_all", fallback)


def current_query_one(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_query_one", fallback)


def current_execute(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_execute", fallback)


def current_audit(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_audit", fallback)


def current_load_connection(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_load_connection", fallback)


def current_connection_view(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_connection_view", fallback)


def current_load_tool(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_load_tool", fallback)


def current_tool_view(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_tool_view", fallback)


def current_tool_list_view(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_tool_list_view", fallback)


def current_probe_http_api(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_probe_http_api", fallback)


def current_current_input_schema(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_current_input_schema", fallback)


def current_export_payload(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_export_payload", fallback)


def current_set_connection_enabled(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("_set_connection_enabled", fallback)


def current_run_governance_mutation(fallback: Callable[..., Any]) -> Callable[..., Any]:
    return _resolve_facade_attr("run_governance_mutation", fallback)
