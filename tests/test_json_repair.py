"""tests for multi_agent.planner._extract_json — 4 层 JSON 修复管道"""

import pytest
from multi_agent.planner import _extract_json


def test_valid_json_direct():
    """Layer 0: 正常 JSON 直接解析"""
    result = _extract_json('{"nodes": {"1": {"step_id": "1"}}, "edges": {}}')
    assert result["nodes"]["1"]["step_id"] == "1"


def test_markdown_code_block():
    """Layer 0: markdown 包裹的 JSON"""
    text = '```json\n{"nodes": {}, "edges": {}}\n```'
    result = _extract_json(text)
    assert result["nodes"] == {}
    assert result["edges"] == {}


def test_trailing_comma_in_object():
    """Layer 2: 尾逗号修复 {,}"""
    text = '{"nodes": {"1": {"step_id": "1",}}, "edges": {},}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_trailing_comma_in_array():
    """Layer 2: 数组尾逗号修复 [,]"""
    text = '{"nodes": {"1": {"step_id": "1"}}, "edges": {"2": ["1",]}}'
    result = _extract_json(text)
    assert result["edges"]["2"] == ["1"]


def test_chinese_quotes():
    """Layer 2: 中文引号替换"""
    text = '{"nodes": {“1”: {“step_id”: “1”}}}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_unquoted_keys():
    """Layer 2: 无引号 key 修复 {key: value}"""
    text = '{nodes: {"1": {step_id: "1"}}, edges: {}}'
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_single_quoted_strings():
    """Layer 2: 单引号字符串 -> 双引号"""
    text = "{'nodes': {'1': {'step_id': '1'}}, 'edges': {}}"
    result = _extract_json(text)
    assert result["nodes"]["1"]["step_id"] == "1"


def test_nested_braces():
    """Layer 1: 多层嵌套正确截取"""
    text = '前缀文本 {"nodes": {"1": {"step_id": "1", "params": {"q": "测试"}}}, "edges": {}} 后缀文本'
    result = _extract_json(text)
    assert result["nodes"]["1"]["params"]["q"] == "测试"


def test_empty_string_returns_fallback():
    """全失败时返回空 dict（触发 _fallback_plan）"""
    result = _extract_json("这不是 JSON 文本")
    assert result == {}


def test_planner_real_output_like():
    """模拟 Planner 常见输出"""
    text = '''```json
{
  "nodes": {
    "1": {
      "step_id": "1",
      "capability": "query_database",
      "description": "查询技术部员工",
      "params": {"question": "技术部有哪些员工"}
    },
    "2": {
      "step_id": "2",
      "capability": "search_knowledge",
      "description": "检索请假制度",
      "params": {"question": "请假流程和规定"}
    }
  },
  "edges": {
    "3": ["1", "2"]
  }
}
```'''
    result = _extract_json(text)
    assert len(result["nodes"]) == 2
    assert result["nodes"]["1"]["capability"] == "query_database"
    assert result["nodes"]["2"]["capability"] == "search_knowledge"
    assert "3" in result["edges"]
