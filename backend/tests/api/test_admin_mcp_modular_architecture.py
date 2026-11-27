"""test_admin_mcp_modular_architecture.py — MCP 管理控制台单一职责架构守卫测试。

验证 Tier 11 解耦重构质量：
1. 门面文件 backend/app/api/routes/admin_mcp.py 行数严格控制在 200 行以内；
2. 领域子包 admin_mcp_subroutes/ 模块完备性与单一职责划分；
3. 契约兼容性：公开/内部符号完备导出，路由 prefix 与 tags 保持原样；
4. FacadeProxy 动态穿透力：验证外部对门面属性进行 monkeypatch 时，子路由能够动态感知。
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest
from fastapi import APIRouter

from backend.app.api.routes import admin_mcp
from backend.app.api.routes import admin_mcp_subroutes


def test_facade_file_size_limit():
    """门面代码行数上限守卫（不得超过 200 行，当前目标 < 150 行）。"""
    facade_path = Path(admin_mcp.__file__)
    lines = facade_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 200, f"admin_mcp.py 门面超过 200 行红线: {len(lines)} 行"


def test_subroutes_package_structure():
    """子模块完备性验证。"""
    subroutes_dir = Path(admin_mcp_subroutes.__file__).parent
    expected_files = {
        "__init__.py",
        "models.py",
        "dynamic.py",
        "common.py",
        "overview_routes.py",
        "connection_routes.py",
        "tool_routes.py",
        "semantic_routes.py",
        "routes.py",
    }
    actual_files = {p.name for p in subroutes_dir.glob("*.py")}
    missing = expected_files - actual_files
    assert not missing, f"子包缺少必要模块: {missing}"


def test_contract_symbols_exported():
    """对外公开与内部契约导出完备性。"""
    required_symbols = [
        "router",
        "OperatorIdentity",
        "require_admin_user",
        "run_governance_mutation",
        "resolve_identity",
        "McpClientError",
        "mcp_list_tools",
        "current_llm_model",
        "invoke_admin_llm",
        "_redact_urls",
        "encrypt_secret",
        "fingerprint",
        "last4",
        "logger",
        "_MutationConflict",
        "ConnectionCreate",
        "ConnectionUpdate",
        "ReviewRequest",
        "GrantRequest",
        "TestInvokeRequest",
        "HttpToolRegisterRequest",
        "HttpToolInvocationUpdateRequest",
        "SemanticDraftRequest",
        "SEMANTIC_SUMMARY_MAX",
        "SEMANTIC_ITEM_MAX",
        "SEMANTIC_LIST_MAX",
        "SEMANTIC_HINT_MAX",
        "_SENSITIVE_QUERY_KEYS",
        "_CONNECTION_SELECT",
        "_TOOL_SELECT",
        "_db",
        "_query_all",
        "_query_one",
        "_execute",
        "_audit",
        "_tenant_of",
        "_actor_of",
        "_bad_request",
        "_not_found",
        "_conflict",
        "_validate_connection_url",
        "_validate_egress_allowlist",
        "_now_iso",
        "_normalize_semantic_draft",
        "_validate_semantic_contract",
        "_render_semantic_prompt",
        "_embedded_credential_reason",
        "_sanitize_export_url",
        "_load_connection",
        "_connection_view",
        "_connection_secret",
        "_conn_headers",
        "_refresh_runtime",
        "_mark_connection_status",
        "_record_probe_result",
        "_probe_http_api",
        "_classify_mcp_error",
        "_upsert_credential",
        "_load_tool",
        "_tool_view",
        "_tool_list_view",
        "_current_input_schema",
        "_export_payload",
        "_set_connection_enabled",
        "mcp_overview",
        "list_connections",
        "create_connection",
        "get_connection",
        "update_connection",
        "delete_connection",
        "test_connection",
        "register_http_tool",
        "update_http_tool_invocation",
        "discover_tools",
        "enable_connection",
        "disable_connection",
        "list_connection_tools",
        "list_all_tools",
        "get_tool",
        "review_tool",
        "disable_tool",
        "suggest_semantic",
        "save_semantic_draft",
        "approve_semantic",
        "grant_tool",
        "test_invoke_tool",
        "health_overview",
        "export_configuration",
    ]
    for sym in required_symbols:
        assert hasattr(admin_mcp, sym), f"门面 admin_mcp 缺失符号: {sym}"
        assert hasattr(admin_mcp_subroutes, sym), f"子包 admin_mcp_subroutes 缺失符号: {sym}"


def test_router_configuration():
    """验证 APIRouter 配置与端点数量。"""
    assert isinstance(admin_mcp.router, APIRouter)
    assert admin_mcp.router.prefix == "/admin/mcp"
    assert "Admin-MCP" in admin_mcp.router.tags

    # 原单体共 19 个端点
    endpoints = [route.path for route in admin_mcp.router.routes]
    assert len(endpoints) >= 19
    assert "/admin/mcp/overview" in endpoints
    assert "/admin/mcp/connections" in endpoints
    assert "/admin/mcp/connections/{conn_id}" in endpoints
    assert "/admin/mcp/connections/{conn_id}/test" in endpoints
    assert "/admin/mcp/tools" in endpoints
    assert "/admin/mcp/tools/{tool_id}" in endpoints
    assert "/admin/mcp/tools/{tool_id}/review" in endpoints
    assert "/admin/mcp/tools/{tool_id}/semantic/suggest" in endpoints
    assert "/admin/mcp/health" in endpoints
    assert "/admin/mcp/export" in endpoints


def test_dynamic_monkeypatch_penetration(monkeypatch):
    """验证 FacadeProxy 对 monkeypatch 动态穿透的支持。"""
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import (
        current_load_tool,
        current_query_one,
    )

    dummy_tool = {"id": 999, "tool_name": "mock_tool"}
    monkeypatch.setattr(admin_mcp, "_load_tool", lambda tid, tenant: dummy_tool)

    load_fn = current_load_tool(admin_mcp_subroutes.common._load_tool)
    result = load_fn(999, "default")
    assert result == dummy_tool
