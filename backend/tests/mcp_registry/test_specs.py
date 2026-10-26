"""mcp_registry.specs 单测：上游不可信输入的归一、治理派生与安全底线。

纯函数测试，无 DB / 无网络。
"""
from __future__ import annotations

import pytest

from backend.core.tool_governance.models import ApprovalPolicy, Operation, RiskLevel
from backend.infra.mcp_registry import specs


class TestSlug:
    def test_capability_slug_is_valid_two_segment(self):
        slug = specs.capability_slug("Zhihu Open-API", "query-tickets.v2")
        assert specs.is_valid_slug(slug)
        assert slug == "zhihu_open_api__query_tickets_v2"

    def test_slugify_collapses_and_strips(self):
        assert specs.slugify("  Hello -- World!  ") == "hello_world"
        assert specs.slugify("123abc") == "t123abc"  # 数字不开头
        assert specs.slugify("---") == "tool"

    def test_same_conn_and_tool_yield_same_slug(self):
        assert (specs.capability_slug("a", "b")
                == specs.capability_slug("a", "b"))

    def test_invalid_slug_rejected(self):
        assert not specs.is_valid_slug("a.b")
        assert not specs.is_valid_slug("a-b")
        assert not specs.is_valid_slug("")


class TestContentHash:
    def test_stable_and_canonical(self):
        a = specs.content_hash("d", {"type": "object", "properties": {}}, None)
        b = specs.content_hash("d", {"properties": {}, "type": "object"}, {})
        assert a == b  # 键序无关

    def test_sensitive_to_schema_change(self):
        a = specs.content_hash("d", {"properties": {"q": {"type": "string"}}})
        b = specs.content_hash("d", {"properties": {"q": {"type": "number"}}})
        assert a != b

    def test_sensitive_to_description(self):
        assert (specs.content_hash("a", {}) != specs.content_hash("b", {}))


class TestNormalizeInputSchema:
    def test_forces_object_type_and_closed_props(self):
        schema = specs.normalize_input_schema({"properties": {"q": {}}})
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False

    def test_missing_properties_gets_empty(self):
        schema = specs.normalize_input_schema(None)
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is False


class TestDeriveToolSpec:
    def _base_governance(self, **overrides):
        gov = {"operation": "read", "risk_level": "R1",
               "confirmation_policy": "none"}
        gov.update(overrides)
        return gov

    def test_defaults_from_minimal_governance(self):
        spec = specs.derive_tool_spec(
            "mcp.srv__t", "描述",
            {"type": "object", "properties": {"q": {"type": "string"}}},
            self._base_governance())
        assert spec.capability == "mcp.srv__t"
        assert spec.domains == ("mcp",)
        assert spec.operation is Operation.READ
        assert spec.risk_level is RiskLevel.R1
        assert spec.params_schema["additionalProperties"] is False
        assert spec.timeout_ms == 15_000

    def test_invalid_operation_rejected(self):
        with pytest.raises(specs.SpecDeriveError):
            specs.derive_tool_spec(
                "mcp.srv__t", "d", None,
                self._base_governance(operation="explode"))

    def test_invalid_risk_rejected(self):
        with pytest.raises(specs.SpecDeriveError):
            specs.derive_tool_spec(
                "mcp.srv__t", "d", None, self._base_governance(risk_level="R9"))

    def test_high_risk_without_confirmation_rejected(self):
        # 与静态注册表同等安全底线：R2/R3 必须带用户确认
        with pytest.raises(specs.SpecDeriveError):
            specs.derive_tool_spec(
                "mcp.srv__t", "d", None,
                self._base_governance(risk_level="R2",
                                      confirmation_policy="none"))

    def test_high_risk_with_confirmation_allowed(self):
        spec = specs.derive_tool_spec(
            "mcp.srv__t", "d", None,
            self._base_governance(risk_level="R3", confirmation_policy="dual"))
        assert spec.requires_confirmation
        assert spec.requires_admin_approval

    def test_write_operation_forces_zero_retry_and_idempotency(self):
        spec = specs.derive_tool_spec(
            "mcp.srv__t", "d", None,
            self._base_governance(
                operation="update",
                retry={"max_attempts": 5},  # 写操作重试必须被覆盖为 0
                idempotency={"required": False}))
        assert spec.retry_max_attempts == 0
        assert spec.idempotency_required is True

    def test_read_operation_keeps_retry_config(self):
        spec = specs.derive_tool_spec(
            "mcp.srv__t", "d", None,
            self._base_governance(retry={"max_attempts": 2}))
        assert spec.retry_max_attempts == 2

    def test_required_roles_passthrough(self):
        spec = specs.derive_tool_spec(
            "mcp.srv__t", "d", None,
            self._base_governance(required_roles=["admin", "editor"]))
        assert spec.required_roles == ("admin", "editor")


class TestDeriveCapabilityDecl:
    def test_two_examples_routed(self):
        decl = specs.derive_capability_decl(
            "mcp.srv__t", "描述", None,
            {"risk_level": "R1", "planner_example": {"query": "x"}},
            ["示例一", "示例二"])
        assert decl.routed is True
        assert decl.domain == "mcp"
        assert decl.skill == "mcp_dynamic"
        assert decl.risk_level == "LOW"
        assert len(decl.planner_examples) == 1

    def test_insufficient_examples_not_routed(self):
        decl = specs.derive_capability_decl(
            "mcp.srv__t", "描述", None, {"risk_level": "R1"}, ["唯一示例"])
        assert decl.routed is False
        assert "路由示例" in decl.reason

    def test_risk_mapping_to_router_levels(self):
        assert specs.derive_capability_decl(
            "mcp.a", "d", None, {"risk_level": "R2"}, []).risk_level == "MEDIUM"
        assert specs.derive_capability_decl(
            "mcp.a", "d", None, {"risk_level": "R3"}, []).risk_level == "HIGH"
        # HIGH 风险永不进 Fast Path
        assert specs.derive_capability_decl(
            "mcp.a", "d", None, {"risk_level": "R3"}, []).fast_path_enabled is False

    def test_description_truncated(self):
        decl = specs.derive_capability_decl(
            "mcp.a", "长" * 2000, None, {}, [])
        assert len(decl.description) <= specs.MAX_DESCRIPTION_CHARS
