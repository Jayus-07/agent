"""admin_mcp.py — MCP 统一管理控制台 API（Tier 11 模块化解耦门面）。

职责范围（按单一职责原则解耦至 admin_mcp_subroutes/）：
  - 概览与监控 (overview_routes.py)：/overview, /health, /export
  - 连接管理 (connection_routes.py)：/connections/*, /discover, /test, /enable, /disable, /tools/manual
  - 工具目录与治理 (tool_routes.py)：/tools/*, /review, /disable, /grants, /test-invoke
  - 语义契约候选 (semantic_routes.py)：/tools/{id}/semantic/*

安全红线（ADR-0011 D7）：
  - 凭据密文（secret_cipher）绝不进入 GET 响应 / export / prompt；
  - 发现不直接等同于可用：未审核工具永不进入运行时快照；
  - 审核绑定所见版本（CAS）：content_hash 失配即退出运行时；
  - 统一经 APISIX 网关进入，权限门禁集中由 deps.require_admin_user 承担；
  - 外部测试对本门面属性的 monkeypatch 经 FacadeProxy 动态穿透至子模块。
"""
from __future__ import annotations

from backend.app.api.routes.admin_mcp_subroutes import (
    McpClientError,
    OperatorIdentity,
    SEMANTIC_HINT_MAX,
    SEMANTIC_ITEM_MAX,
    SEMANTIC_LIST_MAX,
    SEMANTIC_SUMMARY_MAX,
    ConnectionCreate,
    ConnectionUpdate,
    GrantRequest,
    HttpToolInvocationUpdateRequest,
    HttpToolRegisterRequest,
    ReviewRequest,
    SemanticDraftRequest,
    TestInvokeRequest,
    _CONNECTION_SELECT,
    _MutationConflict,
    _SENSITIVE_QUERY_KEYS,
    _TOOL_SELECT,
    _actor_of,
    _audit,
    _bad_request,
    _classify_mcp_error,
    _conflict,
    _conn_headers,
    _connection_secret,
    _connection_view,
    _current_input_schema,
    _db,
    _embedded_credential_reason,
    _execute,
    _export_payload,
    _load_connection,
    _load_tool,
    _mark_connection_status,
    _normalize_semantic_draft,
    _not_found,
    _now_iso,
    _probe_http_api,
    _query_all,
    _query_one,
    _record_probe_result,
    _redact_urls,
    _refresh_runtime,
    _render_semantic_prompt,
    _sanitize_export_url,
    _set_connection_enabled,
    _tenant_of,
    _tool_list_view,
    _tool_view,
    _upsert_credential,
    _validate_connection_url,
    _validate_egress_allowlist,
    _validate_semantic_contract,
    approve_semantic,
    create_connection,
    current_llm_model,
    delete_connection,
    disable_connection,
    disable_tool,
    discover_tools,
    enable_connection,
    encrypt_secret,
    export_configuration,
    fingerprint,
    get_connection,
    get_tool,
    grant_tool,
    health_overview,
    invoke_admin_llm,
    last4,
    list_all_tools,
    list_connection_tools,
    list_connections,
    logger,
    mcp_list_tools,
    mcp_overview,
    register_http_tool,
    require_admin_user,
    resolve_identity,
    review_tool,
    router,
    run_governance_mutation,
    save_semantic_draft,
    suggest_semantic,
    test_connection,
    test_invoke_tool,
    update_connection,
    update_http_tool_invocation,
)

__all__ = ["router"]
