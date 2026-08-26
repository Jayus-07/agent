"""P1-14 测试 — 统一 JSON 提取器（backend.shared.json_extractor）

覆盖 5 层策略链与三种失败语义（None / {} / 抛异常）。
"""
import pytest

from backend.shared.json_extractor import (
    JsonExtractionError,
    extract_json,
    extract_json_or_empty,
    extract_json_strict,
)


class TestLayerStrategies:
    """各层策略逐层验证"""

    def test_layer0_direct(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_layer1_markdown_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert extract_json(text) == {"a": 1}

    def test_layer1_fence_no_lang_tag(self):
        text = '```\n{"a": 1}\n```'
        assert extract_json(text) == {"a": 1}

    def test_layer2_outer_braces_with_prefix(self):
        text = '好的，结果如下：{"a": 1, "b": [2, 3]} 以上。'
        assert extract_json(text) == {"a": 1, "b": [2, 3]}

    def test_layer3_trailing_comma_repaired(self):
        text = '{"a": 1, "b": 2,}'
        assert extract_json(text) == {"a": 1, "b": 2}

    def test_layer3_chinese_quotes_repaired(self):
        text = '{“a”: “值”}'
        assert extract_json(text) == {"a": "值"}

    def test_layer3_unquoted_keys_repaired(self):
        # 未加引号的 key 可修复；值本身仍需是合法 JSON 字面量
        text = '{execution_mode: "plan", score: 0.9}'
        result = extract_json(text)
        assert result is not None
        assert result.get("execution_mode") == "plan"

    def test_layer3_single_quotes_repaired(self):
        text = "{'mode': 'direct'}"
        assert extract_json(text) == {"mode": "direct"}

    def test_layer4_brute_force_nested(self):
        text = '前缀文字 {"outer": {"inner": 1}} 后缀 {"tiny": 2}'
        result = extract_json(text)
        assert result is not None
        # 按长度降序，优先取更长的嵌套对象
        assert result.get("outer") == {"inner": 1}

    def test_nested_object_in_fence(self):
        text = '```json\n{"candidates": [{"name": "sql.query", "score": 0.9}]}\n```'
        result = extract_json(text)
        assert result["candidates"][0]["name"] == "sql.query"

    def test_non_dict_json_returns_none(self):
        """顶层数组/标量不是 dict — 不返回（调用方契约是 dict）"""
        assert extract_json('[1, 2, 3]') is None
        assert extract_json('"just a string"') is None

    def test_empty_and_none_input(self):
        assert extract_json("") is None
        assert extract_json(None) is None
        assert extract_json("没有任何大括号") is None


class TestFailureSemantics:
    """三种失败语义"""

    def test_or_empty_returns_dict(self):
        assert extract_json_or_empty("not json at all") == {}

    def test_or_empty_passthrough(self):
        assert extract_json_or_empty('{"ok": true}') == {"ok": True}

    def test_strict_raises(self):
        with pytest.raises(JsonExtractionError):
            extract_json_strict("not json at all")

    def test_strict_is_value_error_subclass(self):
        """analyzer 原实现抛 ValueError — 兼容既有 except 子句"""
        with pytest.raises(ValueError):
            extract_json_strict("!!!")

    def test_strict_passthrough(self):
        assert extract_json_strict('{"a": 1}') == {"a": 1}


class TestCallSiteCompatibility:
    """验证收敛调用点的行为契约

    注：planner._extract_json 因既有循环依赖（agents → orchestration →
    agents）不在单测中直接导入，其包装逻辑仅 3 行委托 + 告警，
    由 shared 提取器测试 + 集成回归覆盖。
    """

    def test_llm_router_wrapper_keeps_none_semantics(self):
        from backend.orchestration.router.llm_router import _extract_json
        assert _extract_json("完全不是 JSON") is None
        assert _extract_json('{"candidates": []}') == {"candidates": []}

    def test_analyzer_wrapper_keeps_raise_semantics(self):
        from backend.skills.business_analysis.analyzer import _extract_json
        with pytest.raises(ValueError):
            _extract_json("完全不是 JSON")
        assert _extract_json('{"insight": "x"}') == {"insight": "x"}
