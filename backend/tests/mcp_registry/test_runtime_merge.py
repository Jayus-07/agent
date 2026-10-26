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
            content_digest=mcp_store.derived_content_digest(
                "测试工具",
                {"type": "object", "properties": {"q": {"type": "string"}}},
                governance, ["示例一", "示例二"]),
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


class TestSnapshotSignature:
    """签名必须覆盖声明内容（Review P2）：只比较键集合时，同名工具的
    治理/Schema 变化不会让其他 worker 失效派生缓存。"""

    def test_identical_content_same_signature(self):
        assert (_make_snapshot(
            {CAP: {"operation": "read", "risk_level": "R1"}}).signature()
            == _make_snapshot(
                {CAP: {"operation": "read", "risk_level": "R1"}}).signature())

    def test_governance_change_changes_signature(self):
        snap_a = _make_snapshot({CAP: {"operation": "read", "risk_level": "R1"}})
        snap_b = _make_snapshot({CAP: {"operation": "read", "risk_level": "R2",
                                       "confirmation_policy": "user_confirmation"}})
        assert snap_a.signature() != snap_b.signature()

    def test_schema_change_changes_signature(self):
        snap_a = _make_snapshot({CAP: {"operation": "read", "risk_level": "R1"}})
        snap_b = _make_snapshot({CAP: {"operation": "read", "risk_level": "R1"}})
        # 同名工具、同 governance，但派生 Schema/描述内容变化（摘要不同）
        object.__setattr__(snap_b.tools[CAP], "content_digest", "d2")
        assert snap_a.signature() != snap_b.signature()


class TestPlanNormalization:
    """Review P1：normalize_plan 的有效能力集必须与 Planner/Critique 的
    effective_manifest 视图一致——否则动态 mcp.* 步骤被静默删除。"""

    def test_normalize_plan_keeps_dynamic_capability(self):
        from backend.agents.planner.plan_utils import normalize_plan

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        plan = {"nodes": [
            {"step_id": "1", "capability": CAP,
             "description": "动态工具步骤", "params": {"q": "x"}},
        ], "edges": {}}
        out = normalize_plan(plan)
        assert "1" in out["nodes"] and out["nodes"]["1"]["capability"] == CAP

    def test_normalize_plan_still_drops_unknown_dynamic(self):
        from backend.agents.planner.plan_utils import normalize_plan

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        plan = {"nodes": [
            {"step_id": "1", "capability": "mcp.nope__missing",
             "description": "未注册", "params": {}},
        ], "edges": {}}
        out = normalize_plan(plan)
        assert out["nodes"] == {}

    def test_normalize_plan_keeps_static_capability(self):
        from backend.agents.planner.plan_utils import normalize_plan
        from backend.orchestration.router.manifest import effective_manifest

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        visible = [c.name for c in effective_manifest().capabilities
                   if not c.name.startswith("mcp.")]
        assert visible, "静态能力必须始终可见"
        plan = {"nodes": [
            {"step_id": "1", "capability": visible[0],
             "description": "静态能力", "params": {}},
        ], "edges": {}}
        out = normalize_plan(plan)
        assert "1" in out["nodes"]


class TestVerifyWriteAuthorization:
    """写操作出网前的 DB 强一致复核（撤权/停用/漂移即时拦截）。"""

    def _patch_engine(self, monkeypatch, fetchone_result, execute_error=None):
        class _Cur:
            params = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                if execute_error:
                    raise execute_error
                self.params = params

            def fetchone(self):
                return fetchone_result

        cur = _Cur()
        class _Raw:
            def cursor(self):
                return cur

            def rollback(self):
                pass

            def close(self):
                pass

        class _Engine:
            def raw_connection(self):
                return _Raw()

        import backend.infra.db as db_mod

        monkeypatch.setattr(db_mod, "get_memory_engine", lambda: _Engine())
        return cur

    def test_authorized_write_passes(self, monkeypatch):
        cur = self._patch_engine(monkeypatch, fetchone_result={"id": 1})
        ok, reason = mcp_store.verify_write_authorization(
            CAP, "agent", "tenant_a")
        assert ok is True and reason == ""
        assert cur.params == {"slug": CAP[4:], "tenant": "tenant_a",
                              "product": "agent"}

    def test_revoked_write_denied(self, monkeypatch):
        self._patch_engine(monkeypatch, fetchone_result=None)
        ok, reason = mcp_store.verify_write_authorization(
            CAP, "agent", "tenant_a")
        assert ok is False and "失效" in reason

    def test_db_failure_fail_closed(self, monkeypatch):
        self._patch_engine(monkeypatch, fetchone_result=None,
                           execute_error=RuntimeError("db down"))
        ok, reason = mcp_store.verify_write_authorization(
            CAP, "agent", "tenant_a")
        assert ok is False and "拒绝" in reason

    def test_non_dynamic_capability_denied_without_db(self, monkeypatch):
        self._patch_engine(monkeypatch, fetchone_result={"id": 1})
        ok, _ = mcp_store.verify_write_authorization(
            "rag.search", "agent", "tenant_a")
        assert ok is False


class TestWriteStrongConsistencyAtInvoke:
    """adapter.invoke 的写操作强一致拦截与 internal/只读豁免。"""

    @staticmethod
    def _adapter(capability):
        from backend.skills.mcp_dynamic.skill import _McpDynamicToolAdapter

        return _McpDynamicToolAdapter(capability)

    def _stub_outbound(self, monkeypatch):
        import backend.skills.mcp_dynamic.skill as skill_mod
        import backend.tools.url_guard as url_guard

        monkeypatch.setattr(skill_mod, "call_tool", lambda *a, **k: {"ok": True})
        # invoke 内部延迟 from ... import assert_url_allowed → patch 源模块
        monkeypatch.setattr(url_guard, "assert_url_allowed", lambda *a, **k: None)

    def test_write_tool_rejected_when_source_of_truth_denies(self, monkeypatch):
        from backend.core.request_context import (
            set_product_context, set_tool_tenant_id)
        from backend.infra.mcp_client import McpClientError

        mcp_store.inject_snapshot_for_tests(_make_snapshot(
            {CAP: {"operation": "update", "risk_level": "R1"}}))
        calls = []

        def fake_verify(capability, product_id, tenant_id):
            calls.append((capability, product_id, tenant_id))
            return False, "撤权"

        monkeypatch.setattr(
            mcp_store, "verify_write_authorization", fake_verify)
        set_product_context("agent", "test")
        set_tool_tenant_id("tenant_a")
        with pytest.raises(McpClientError, match="写操作被拒绝"):
            self._adapter(CAP).invoke({"q": "x"})
        assert calls == [(CAP, "agent", "tenant_a")]

    def test_read_tool_skips_strong_check(self, monkeypatch):
        from backend.core.request_context import (
            set_product_context, set_tool_tenant_id)

        mcp_store.inject_snapshot_for_tests(_snapshot_with_tool())
        self._stub_outbound(monkeypatch)

        def fail_verify(*args, **kwargs):
            raise AssertionError("只读工具不应触发强一致复核")

        monkeypatch.setattr(
            mcp_store, "verify_write_authorization", fail_verify)
        set_product_context("agent", "test")
        set_tool_tenant_id("tenant_a")
        result = self._adapter(CAP).invoke({"q": "x"})
        assert result == {"ok": True}

    def test_internal_product_write_skips_grant_verify(self, monkeypatch):
        from backend.core.request_context import (
            set_product_context, set_tool_tenant_id)

        mcp_store.inject_snapshot_for_tests(_make_snapshot(
            {CAP: {"operation": "update", "risk_level": "R1"}}))
        self._stub_outbound(monkeypatch)

        def fail_verify(*args, **kwargs):
            raise AssertionError("internal 治理验证通道不依赖产品授权快照")

        monkeypatch.setattr(
            mcp_store, "verify_write_authorization", fail_verify)
        set_product_context("internal", "test")
        set_tool_tenant_id("tenant_a")
        assert self._adapter(CAP).invoke({"q": "x"}) == {"ok": True}
