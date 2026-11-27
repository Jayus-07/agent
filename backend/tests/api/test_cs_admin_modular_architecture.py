"""架构守卫测试：确保 cs_admin 保持单一职责与模块化架构，防止代码膨胀与契约漂移。"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import APIRouter


def test_facade_file_size_boundary():
    """门面守卫：backend/app/api/routes/cs_admin.py 必须是轻量门面（不超过 200 行）。"""
    facade_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "routes"
        / "cs_admin.py"
    )
    assert facade_path.exists(), f"Facade file not found: {facade_path}"

    line_count = len(facade_path.read_text(encoding="utf-8").splitlines())
    assert (
        line_count <= 200
    ), f"cs_admin.py 超过了 200 行门面限制 (当前: {line_count} 行)，请勿在门面堆砌实现逻辑！"


def test_subroutes_structure_complete():
    """子领域路由包结构守卫：必须包含全部 7 个专职领域模块。"""
    subroutes_dir = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "routes"
        / "cs_admin_subroutes"
    )
    assert subroutes_dir.is_dir(), f"Subroutes package not found: {subroutes_dir}"

    expected_files = {
        "__init__.py",
        "base.py",
        "schemas.py",
        "deps.py",
        "confirm.py",
        "conversations.py",
        "handoff.py",
        "user_messages.py",
    }
    actual_files = {p.name for p in subroutes_dir.glob("*.py")}
    assert expected_files.issubset(
        actual_files
    ), f"缺少领域子模块: {expected_files - actual_files}"


def test_facade_symbols_re_exported():
    """兼容性守卫：门面必须完整重新导出全部 Schema、关键函数与常量。"""
    from backend.app.api.routes import cs_admin

    expected_schemas = [
        "ConfirmActionBody",
        "RefundDraftUpdateBody",
        "MessageDTO",
        "ConversationSummary",
        "ConversationDetail",
        "PaginatedConversations",
        "MyConversationMessage",
        "MyConversationItem",
        "MyConversationsResponse",
        "RatingRequest",
        "CSStatsResponse",
        "MyMessageBody",
        "HandoffQueueItem",
        "HandoffQueueResponse",
        "ClaimRequest",
        "AgentMessageRequest",
        "TypingRequest",
        "AgentMessageResponse",
        "HandoffMeta",
        "HandoffMessagesResponse",
    ]
    for schema_name in expected_schemas:
        assert hasattr(
            cs_admin, schema_name
        ), f"cs_admin 门面缺少导出 Schema: {schema_name}"

    expected_helpers = [
        "_use_java_source",
        "_is_service_channel",
        "_is_cs_operator",
        "_require_cs_operator",
        "_resolve_tenant_scope",
        "_require_operator_and_tenant",
        "_resolve_agent_identity",
        "_resolve_agent_identity_and_tenant",
        "_lookup_bound_agent_id",
        "_ensure_conversation_access",
        "_assert_current_agent",
        "_load_conversation_access_row",
        "_ensure_pending_proposal_access",
        "_proxy_to_java",
        "_resolve_ws_agent_identity",
        "_ensure_my_conversation",
        "_ensure_cs_agent",
        "_load_assigned_agent_id",
        "_async_list_conversations",
        "_async_cs_stats",
        "_async_get_conversation",
        "_async_get_trace_ids",
        "_async_messages_since",
        "_async_handoff_queue",
        "_async_claim",
        "_async_agent_message",
        "_async_save_my_message",
        "_async_my_conversations",
    ]
    for helper_name in expected_helpers:
        assert hasattr(
            cs_admin, helper_name
        ), f"cs_admin 门面缺少导出 Helper: {helper_name}"

    assert hasattr(cs_admin, "_CS_OPERATOR_ROLES"), "缺少 _CS_OPERATOR_ROLES"
    assert hasattr(cs_admin, "router"), "缺少 router 实例"
    assert hasattr(cs_admin, "confirm_router"), "缺少 confirm_router 实例"


def test_routes_mounted_and_path_parity():
    """路由挂载与路径一致性守卫：确保 router 与 confirm_router 路由数量及关键路径一致。"""
    from backend.app.api.routes import cs_admin

    router_paths = {r.path for r in cs_admin.router.routes}
    assert "/cs/conversations/" in router_paths
    assert "/cs/conversations/stats" in router_paths
    assert "/cs/conversations/my" in router_paths
    assert "/cs/conversations/handoff/queue" in router_paths
    assert "/cs/conversations/{conversation_id}" in router_paths
    assert "/cs/conversations/{conversation_id}/events" in router_paths
    assert "/cs/conversations/{conversation_id}/messages" in router_paths
    assert len(cs_admin.router.routes) >= 20

    confirm_paths = {r.path for r in cs_admin.confirm_router.routes}
    assert "/cs/confirm" in confirm_paths
    assert "/cs/conversations/my/{conversation_id}/refund-draft" in confirm_paths
    assert "/cs/refund-requests" in confirm_paths
    assert "/cs/refund-requests/{request_id}" in confirm_paths
    assert len(cs_admin.confirm_router.routes) >= 4


def test_facade_proxy_monkeypatch_penetration(monkeypatch):
    """动态穿透守卫：验证外部 monkeypatch cs_admin 内部函数能够穿透至领域子模块。"""
    from backend.app.api.routes import cs_admin
    from backend.app.api.routes.cs_admin_subroutes.base import facade

    called = False

    async def fake_lookup(*args, **kwargs):
        nonlocal called
        called = True
        return "fake-agent-999"

    monkeypatch.setattr(cs_admin, "_lookup_bound_agent_id", fake_lookup)

    import asyncio

    res = asyncio.run(facade._lookup_bound_agent_id(tenant_id="t1", user_id="u1"))
    assert res == "fake-agent-999"
    assert called is True
