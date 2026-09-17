# -*- coding: utf-8 -*-
"""tool_schema 转换器 + validate_params（integer 别名/auto 参数）单测。"""
import pytest

from backend.orchestration.capability_registry import tool_registry
from backend.orchestration.tool_schema import (
    capabilities_to_tools,
    capability_to_function,
    capability_to_function_name,
    function_name_to_capability,
)
from backend.skills.base import validate_params


class TestNameMapping:
    def test_dot_replaced_and_roundtrip(self):
        assert capability_to_function_name("sql.query") == "sql__query"
        assert function_name_to_capability("sql__query") == "sql.query"
        assert function_name_to_capability(
            capability_to_function_name("competitor.analyze")
        ) == "competitor.analyze"

    def test_illegal_name_rejected(self):
        # 含空格/点以外非法字符的 capability 名无法转为合法 function name
        with pytest.raises(ValueError):
            capability_to_function_name("bad name!")


class TestCapabilityToFunction:
    def test_all_registered_capabilities_convert(self):
        caps = tool_registry.get_available_capabilities()
        for cap in caps:
            fn = capability_to_function(cap)
            assert fn is not None, cap
            assert fn["type"] == "function"
            params = fn["function"]["parameters"]
            assert params["type"] == "object"
            assert set(params["properties"].keys()) <= set(
                tool_registry.get_schema(cap)["params"].keys()
            )

    def test_report_generate_enum_and_required(self):
        fn = capability_to_function("report.generate")
        props = fn["function"]["parameters"]["properties"]
        assert props["report_type"]["enum"] == [
            "daily_sales", "product_performance", "inventory_health",
            "ad_performance", "order_fulfillment", "customer_analysis",
        ]
        assert fn["function"]["parameters"]["required"] == ["report_type"]

    def test_integer_type_normalized(self):
        """web_search 声明的 "integer"（JSON Schema 风格）应原样保留为 integer"""
        props = capability_to_function("web.search")["function"]["parameters"]["properties"]
        assert props["num_results"]["type"] == "integer"

    def test_auto_param_excluded(self):
        """business.analyze 的 sql_result（auto，由 previous_outputs 注入）不暴露给模型"""
        params = capability_to_function("business.analyze")["function"]["parameters"]
        assert params["properties"] == {}
        assert params["required"] == []

    def test_unregistered_returns_none(self):
        assert capability_to_function("not.registered") is None

    def test_description_contains_example(self):
        fn = capability_to_function("report.generate")
        assert "report_type" in fn["function"]["description"]


class TestValidateParams:
    """integer 别名修复：此前 web_search 的 "integer" 声明被静默跳过校验。"""

    SCHEMA = {"num_results": {"type": "integer", "required": False}}

    def test_integer_value_passes(self):
        assert validate_params(self.SCHEMA, {"num_results": 3}) is None

    def test_string_value_rejected(self):
        err = validate_params(self.SCHEMA, {"num_results": "很多"})
        assert err and "类型应为 integer" in err

    def test_bool_rejected_for_integer(self):
        err = validate_params(self.SCHEMA, {"num_results": True})
        assert err is not None

    def test_auto_param_skipped(self):
        """auto 参数由运行时注入，缺参不算校验失败"""
        schema = {"sql_result": {"type": "object", "required": True, "auto": True}}
        assert validate_params(schema, {}) is None

    def test_real_business_analyze_schema_accepts_empty(self):
        schema = tool_registry.get_schema("business.analyze")["params"]
        assert validate_params(schema, {}) is None


class TestCapabilitiesToTools:
    def test_skips_unregistered_and_builds_map(self):
        tools, fn2cap = capabilities_to_tools(
            ["report.generate", "not.registered", "rag.search"])
        assert len(tools) == 2
        assert fn2cap == {"report__generate": "report.generate",
                          "rag__search": "rag.search"}

    def test_empty_input(self):
        assert capabilities_to_tools([]) == ([], {})
