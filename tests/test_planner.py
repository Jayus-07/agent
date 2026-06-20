"""
planner.py 测试 — 纯逻辑函数（不依赖 LLM）

覆盖:
  - _extract_json(): JSON 提取
  - _normalize_plan(): plan 结构校验
  - _filter_plan(): 后置规则过滤器
  - _fallback_plan(): 空计划兜底
  - is_knowledge_question(): 知识库关键词检测
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.planner import (
    _extract_json,
    _normalize_plan,
    _filter_plan,
    _fallback_plan,
    is_knowledge_question,
)


class TestExtractJson:
    """JSON 从 LLM 输出中提取（4 层修复管道，返回 dict）"""

    def test_pure_json(self):
        text = '{"nodes": [], "edges": {}}'
        result = _extract_json(text)
        assert result == {"nodes": [], "edges": {}}

    def test_json_with_markdown_fence(self):
        text = '```json\n{"nodes": [], "edges": {}}\n```'
        result = _extract_json(text)
        assert "nodes" in result
        assert "edges" in result
        assert result["nodes"] == []

    def test_json_without_lang_specifier(self):
        text = '```\n{"nodes": [], "edges": {}}\n```'
        result = _extract_json(text)
        assert "nodes" in result
        assert result["nodes"] == []

    def test_json_with_prefix_text(self):
        text = '这是分析结果：\n{"nodes": [{"step_id": "1", "capability": "search_knowledge"}]}'
        result = _extract_json(text)
        assert isinstance(result, dict)
        assert "nodes" in result

    def test_no_json(self):
        text = "没有 JSON 内容"
        result = _extract_json(text)
        assert result == {}  # 返回空 dict，触发 _fallback_plan

    def test_nested_braces(self):
        text = '{"nodes": {"1": {"step_id": "1"}}, "edges": {}}'
        result = _extract_json(text)
        assert result["nodes"]["1"]["step_id"] == "1"


class TestNormalizePlan:
    """Plan 结构校验与规范化"""

    def test_normalize_list_nodes(self):
        """旧格式: nodes 是 list"""
        plan = {
            "nodes": [
                {"step_id": "1", "capability": "search_knowledge",
                 "description": "检索", "params": {"question": "test"}},
            ],
            "edges": {},
        }
        result = _normalize_plan(plan)
        assert isinstance(result["nodes"], dict)
        assert "1" in result["nodes"]
        assert result["nodes"]["1"]["capability"] == "search_knowledge"

    def test_normalize_dict_nodes(self):
        """新格式: nodes 已经是 dict"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "query_database",
                      "description": "查询", "params": {"question": "test"}},
            },
            "edges": {},
        }
        result = _normalize_plan(plan)
        assert isinstance(result["nodes"], dict)
        assert "1" in result["nodes"]

    def test_reject_invalid_capability(self):
        """无效 capability 被过滤"""
        plan = {
            "nodes": [
                {"step_id": "1", "capability": "hack_the_planet",
                 "description": "bad", "params": {}},
            ],
            "edges": {},
        }
        result = _normalize_plan(plan)
        assert "1" not in result["nodes"]

    def test_edges_string_to_list(self):
        """edges 的 deps 是字符串时转为列表"""
        plan = {
            "nodes": [
                {"step_id": "1", "capability": "query_database",
                 "description": "q", "params": {}},
                {"step_id": "2", "capability": "generate_report",
                 "description": "r", "params": {}},
            ],
            "edges": {"2": "1"},  # 字符串而非列表
        }
        result = _normalize_plan(plan)
        assert result["edges"]["2"] == ["1"]

    def test_empty_plan(self):
        result = _normalize_plan({"nodes": [], "edges": {}})
        assert result["nodes"] == {}

    def test_mixed_valid_invalid_nodes(self):
        """部分有效 + 部分无效 → 保留有效"""
        plan = {
            "nodes": [
                {"step_id": "1", "capability": "search_knowledge",
                 "description": "ok", "params": {}},
                {"step_id": "2", "capability": "invalid_cap",
                 "description": "bad", "params": {}},
            ],
            "edges": {},
        }
        result = _normalize_plan(plan)
        assert "1" in result["nodes"]
        assert "2" not in result["nodes"]


class TestIsKnowledgeQuestion:
    """知识库关键词检测"""

    def test_standard_keyword(self):
        assert is_knowledge_question("叶菜类能不能第二天卖")  # "能不能"

    def test_definition_question(self):
        assert is_knowledge_question("什么是微服务架构")  # "什么是"

    def test_policy_keyword(self):
        assert is_knowledge_question("预算管理制度有哪些")  # "制度"

    def test_procedure_keyword(self):
        assert is_knowledge_question("请假审批流程是什么")  # "流程"

    def test_pure_data_question(self):
        """纯数据查询不含知识库关键词"""
        assert not is_knowledge_question("技术部有多少人")

    def test_pure_statistics(self):
        assert not is_knowledge_question("统计各部门项目数量")

    def test_pure_comparison(self):
        assert not is_knowledge_question("对比各项目预算")

    def test_how_to_question(self):
        assert is_knowledge_question("怎么配置nginx反向代理")  # "怎么"

    def test_can_question(self):
        assert is_knowledge_question("这个方案是否可行")  # "是否"


class TestFilterPlan:
    """后置规则过滤器"""

    def test_sql_only_plan_unchanged(self):
        """纯 SQL 计划不修改"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "query_database",
                      "description": "查询", "params": {"question": "统计人数"}},
            },
            "edges": {},
        }
        result = _filter_plan(plan, "技术部有多少人")
        assert "1" in result["nodes"]

    def test_rag_only_plan_unchanged(self):
        """纯 RAG 计划（无 SQL）保留"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "search_knowledge",
                      "description": "检索", "params": {"question": "test"}},
            },
            "edges": {},
        }
        result = _filter_plan(plan, "什么是微服务")
        assert "1" in result["nodes"]

    def test_removes_rag_from_mixed_plan_without_keywords(self):
        """混合计划 + 问题不含知识库关键词 → 移除 RAG 步骤"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "query_database",
                      "description": "查询数据", "params": {"question": "查询预算"}},
                "2": {"step_id": "2", "capability": "search_knowledge",
                      "description": "检索知识", "params": {"question": "检索"}},
            },
            "edges": {},
        }
        result = _filter_plan(plan, "统计技术部预算")
        assert "1" in result["nodes"]
        assert "2" not in result["nodes"]  # RAG 步骤被移除

    def test_keeps_rag_when_has_keywords(self):
        """混合计划 + 问题含知识库关键词 → 保留 RAG"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "query_database",
                      "description": "查询", "params": {"question": "查询预算"}},
                "2": {"step_id": "2", "capability": "search_knowledge",
                      "description": "检索制度", "params": {"question": "制度"}},
            },
            "edges": {},
        }
        result = _filter_plan(plan, "查询预算制度规范")
        assert "1" in result["nodes"]
        assert "2" in result["nodes"]  # 有关键词"制度"，保留

    def test_cleans_edges_after_rag_removal(self):
        """移除 RAG 步骤后清理 edges 引用"""
        plan = {
            "nodes": {
                "1": {"step_id": "1", "capability": "query_database",
                      "description": "查询", "params": {}},
                "2": {"step_id": "2", "capability": "search_knowledge",
                      "description": "检索", "params": {}},
                "3": {"step_id": "3", "capability": "generate_report",
                      "description": "报告", "params": {}},
            },
            "edges": {"3": ["1", "2"]},  # report 依赖 sql + rag
        }
        result = _filter_plan(plan, "统计各部门人数")
        # RAG 步骤被移除
        assert "2" not in result["nodes"]
        # report 的 edges 中不再包含已移除的 "2"
        assert result["edges"]["3"] == ["1"]


class TestFallbackPlan:
    """空计划兜底"""

    def test_returns_single_rag_step(self):
        plan = _fallback_plan("叶菜类保鲜期")
        assert len(plan["nodes"]) == 1
        node = list(plan["nodes"].values())[0]
        assert node["capability"] == "search_knowledge"
        assert "叶菜类保鲜期" in node["description"]

    def test_truncates_long_question(self):
        plan = _fallback_plan("A" * 80)
        node = list(plan["nodes"].values())[0]
        assert len(node["description"]) <= 60  # 50 chars + "检索: " prefix

    def test_no_dependencies(self):
        plan = _fallback_plan("test")
        assert plan["edges"] == {}
