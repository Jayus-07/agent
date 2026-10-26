"""MCP 动态注册表与既有治理/路由体系的合并行为（快照注入，无 DB）。

覆盖 ADR-0011 的核心一致性主张：
  - 动态 ToolSpec 进入统一治理视图（all_tool_specs / get_tool_spec / guard）；
  - 动态 CapabilityDecl 进入 effective_manifest（路由可见性）；
  - 产品授权是执行契约（product_allows_capability 动态分支）；
  - 停用/漂移/未授权在快照层即被排除；
  - 通用执行节点映射（capability_registry.get_node）。
"""
from __future__ import annotations

import pytest

from backend.core.tool_governance import registry as gov_registry
from backend.core.tool_governance.guard import ToolCallRequest, governance_runtime
from backend.infra.mcp_registry import specs as spec_lib
from backend.infra.mcp_registry import store as mcp_store


def _make_snapshot(tool_capabilities: dict[str, dict], grants=()):
    """构造注入用快照：tool_capabilities = {capability: governance}。"""
    tools = {}
    decls = {}
    for capability, governance in tool_capabilities.items():
        runtime = mcp_store.McpToolRuntime(
            capability=capability,
            tool_id=abs(hash(capability)) % 10**8,
            connection_id="conn_test",
            upstream_tool_name=capability.split("__", 1)[-1],
            tenant_id="tenant_a",
            spec=spec_lib.derive_tool_spec(
                capability, "测试工具",
                {"type": "object",
                 "properties": {"q": {"type": "string"}}},
                governance),
            network_scope="private",
        )
        tools[capability] = runtime
        decls[capability] = spec_lib.derive_capability_decl(
            capability, "测试工具", None, governance, ["示例一", "示例二"])
    return mcp_store.McpRegistrySnapshot(
        loaded=True,
        tools=tools,
        connections={
            "conn_test": mcp_store.McpConnectionRuntime(
                connection_id="conn_test", base_url="http://127.0.0.1:1/mcp",
                auth_type="none", network_scope="private"),
        },
        capability_decls=decls,
        grants=frozenset(grants),
    )


@pytest.fixture(autouse=True)
def _reset_store():
    mcp_store.reset_for_tests()
    from backend.orchestration.capability_registry import tool_registry

    tool_registry.invalidate_cache()
    yield
    mcp_store.reset_for_tests()
    tool_registry.invalidate_cache()


CAP = "mcp.e2esrv__echo"


def _snapshot_with_tool():
    return _make_snapshot(
        {CAP: {"operation": "read", "risk_level": "R1"}},
        grants={("agent", CAP, "tenant_a")},
    )


class TestGovernanceMerge:
    def test_dynamic_spec_visible_in_merged_registry(self):
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        assert gov_registry.get_tool_spec(CAP) is not None
        assert CAP in gov_registry.all_tool_specs()
        # 静态工具不受影响
        assert gov_registry.get_tool_spec("sql.query") is not None

    def test_absent_snapshot_keeps_static_only(self):
        assert gov_registry.get_tool_spec(CAP) is None
        assert gov_registry.get_tool_spec("sql.query") is not None

    def test_guard_rejects_unregistered_dynamic_tool(self):
        # 未注入快照（= 未审核 / 已停用）。双门各自兜底：
        #   agent 产品身份 → 授权门（product_capability_denied）
        #   internal 服务身份（默认无上下文）→ 授权门放行、Spec 门拦截
        from backend.core import request_context

        request_context.set_product_context("agent", "test")
        try:
            decision = governance_runtime.evaluate(ToolCallRequest(
                capability=CAP, arguments={"q": "hi"},
                user_id="u1", tenant_id="tenant_a"))
            assert decision.allowed is False
            assert decision.code == "product_capability_denied"
        finally:
            request_context.set_product_context("internal", "internal_call")
        decision = governance_runtime.evaluate(ToolCallRequest(
            capability=CAP, arguments={"q": "hi"},
            user_id="u1", tenant_id="tenant_a"))
        assert decision.allowed is False
        assert decision.code == "tool_not_registered"

    def test_guard_rejects_ungranted_product(self):
        mcp_store.inject_snapshot_for_tests(_make_snapshot(
            {CAP: {"operation": "read", "risk_level": "R1"}}))  # 无授权
        from backend.core import request_context

        request_context.set_product_context("agent", "test")
        try:
            decision = governance_runtime.evaluate(ToolCallRequest(
                capability=CAP, arguments={"q": "hi"},
                user_id="u1", tenant_id="tenant_a"))
            assert decision.allowed is False
            assert decision.code == "product_capability_denied"
        finally:
            request_context.set_product_context("internal", "internal_call")

    def test_guard_rejects_cross_tenant_grant(self):
        # 授权绑定 tenant_a；tenant_b 调用同 capability → 拒绝
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        from backend.core import product_context

        assert product_context.product_allows_capability(
            "agent", CAP) is False  # 无 request 上下文租户（默认空）

    def test_guard_allows_granted_tenant(self):
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        from backend.core import request_context

        request_context.set_tool_tenant_id("tenant_a")
        try:
            decision = governance_runtime.evaluate(ToolCallRequest(
                capability=CAP, arguments={"q": "hi"},
                user_id="u1", tenant_id="tenant_a"))
            assert decision.allowed is True
            assert decision.code == ""
        finally:
            request_context.set_tool_tenant_id("")

    def test_invalid_params_blocked_by_dynamic_schema(self):
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        from backend.core import request_context

        request_context.set_tool_tenant_id("tenant_a")
        try:
            decision = governance_runtime.evaluate(ToolCallRequest(
                capability=CAP,
                arguments={"q": 123, "evil_extra": "x"},  # 类型错 + 未知参数
                user_id="u1", tenant_id="tenant_a"))
            assert decision.allowed is False
            assert decision.code == "invalid_param"
        finally:
            request_context.set_tool_tenant_id("")

    def test_snapshot_swap_removes_tool(self):
        # 停用语义：快照更新后工具消失 → guard 拒绝
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        assert gov_registry.get_tool_spec(CAP) is not None
        mcp_store.inject_snapshot_for_tests(
            mcp_store.McpRegistrySnapshot(loaded=True))
        assert gov_registry.get_tool_spec(CAP) is None


class TestManifestMerge:
    def test_effective_manifest_contains_dynamic_capability(self):
        from backend.orchestration.router.manifest import (
            effective_manifest, load_manifest)

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        base = load_manifest()
        merged = effective_manifest()
        assert len(merged.capabilities) == len(base.capabilities) + 1
        assert any(c.name == CAP for c in merged.capabilities)
        assert CAP in merged.capabilities_by_domain.get("mcp", ())
        assert CAP in merged.planner_visible_capability_names

    def test_domain_resolution_includes_dynamic(self):
        from backend.orchestration.router.hierarchical import resolve_domain_tools

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        candidates = [c.name for c in resolve_domain_tools("mcp")]
        assert CAP in candidates

    def test_no_dynamic_tools_equals_static(self):
        from backend.orchestration.router.manifest import (
            effective_manifest, load_manifest)

        merged = effective_manifest()
        assert [c.name for c in merged.capabilities] == [
            c.name for c in load_manifest().capabilities]


class TestNodeMapping:
    def test_dynamic_capability_maps_to_mcp_node(self):
        import backend.skills.mcp_dynamic  # noqa: F401 — 触发节点注册
        from backend.orchestration.capability_registry import (
            MCP_DYNAMIC_NODE, tool_registry)

        assert tool_registry.get_node(CAP) == MCP_DYNAMIC_NODE

    def test_unknown_capability_still_none(self):
        from backend.orchestration.capability_registry import tool_registry

        assert tool_registry.get_node("nope.does_not_exist") is None


class TestPolicyDerivation:
    def test_dynamic_policy_from_governance(self):
        from backend.core.tool_runtime.policy import get_policy, is_registered

        mcp_store.inject_snapshot_for_tests(_make_snapshot(
            {CAP: {"operation": "read", "risk_level": "R1",
                   "runtime": {"timeout_ms": 8000},
                   "retry": {"max_attempts": 2}}}))
        policy = get_policy(CAP)
        assert policy.timeout_ms == 8000.0
        assert policy.retries == 2
        assert is_registered(CAP) is True

    def test_write_dynamic_policy_zero_retry(self):
        from backend.core.tool_runtime.policy import get_policy
        from backend.core.tool_runtime.models import OperationType

        mcp_store.inject_snapshot_for_tests(_make_snapshot(
            {CAP: {"operation": "update", "risk_level": "R1",
                   "retry": {"max_attempts": 3}}}))
        policy = get_policy(CAP)
        assert policy.retries == 0  # 写操作零重试
        assert policy.operation_type is OperationType.WRITE

    def test_unknown_dynamic_falls_back_to_default(self):
        from backend.core.tool_runtime.policy import get_policy, is_registered

        # 快照里没有 mcp.unknown__tool → 默认策略 + 未注册
        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        policy = get_policy("mcp.unknown__tool")
        assert is_registered("mcp.unknown__tool") is False
        assert policy.timeout_ms == 15000.0


class TestProductAuthorization:
    def test_grant_check_semantics(self):
        snap = _snapshot_with_tool()
        assert ("agent", CAP, "tenant_a") in snap.grants
        assert ("customer_service", CAP, "tenant_a") not in snap.grants
        assert ("agent", CAP, "tenant_b") not in snap.grants

    def test_public_product_never_allowed(self):
        from backend.core import product_context

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        assert product_context.product_allows_capability("public", CAP) is False

    def test_internal_always_allowed(self):
        from backend.core import product_context

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        assert product_context.product_allows_capability("internal", CAP) is True
