"""
reporter.py 测试 — 纯逻辑函数（不依赖 LLM）

覆盖:
  - _format_step_outputs(): 步骤输出格式化
  - _extract_rag_references(): 参考文献提取
  - _filter_step_results(): Context Filter（需 mock CrossEncoder）
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.reporter import (
    _format_step_outputs,
    _extract_rag_references,
)


class TestFormatStepOutputs:
    """步骤输出格式化"""

    def test_success_step(self):
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "description": "查询数据",
                  "output": "| id | name |\n| 1 | 技术部 |"},
        }
        result = _format_step_outputs(step_results)
        assert "步骤 1" in result
        assert "✅ 成功" in result
        assert "技术部" in result

    def test_failed_step(self):
        step_results = {
            "1": {"capability": "query_database", "status": "failed",
                  "description": "查询数据",
                  "error": "连接超时"},
        }
        result = _format_step_outputs(step_results)
        assert "❌ 失败" in result
        assert "连接超时" in result

    def test_skipped_step(self):
        step_results = {
            "2": {"capability": "generate_report", "status": "skipped",
                  "description": "生成报告",
                  "error": "前置步骤执行失败"},
        }
        result = _format_step_outputs(step_results)
        assert "⏭️ 已跳过" in result
        assert "前置步骤执行失败" in result

    def test_mixed_steps_sorted(self):
        """步骤按 step_id 排序"""
        step_results = {
            "3": {"capability": "generate_report", "status": "success",
                  "description": "报告", "output": "report content"},
            "1": {"capability": "query_database", "status": "success",
                  "description": "查询", "output": "data"},
        }
        result = _format_step_outputs(step_results)
        idx_1 = result.find("步骤 1")
        idx_3 = result.find("步骤 3")
        assert idx_1 < idx_3  # 步骤 1 在步骤 3 前面

    def test_empty_step_results(self):
        assert _format_step_outputs({}) == ""

    def test_unknown_status(self):
        step_results = {
            "1": {"capability": "search_knowledge", "status": "unknown",
                  "description": "检索"},
        }
        result = _format_step_outputs(step_results)
        assert "⏳ unknown" in result

    def test_long_output_truncated(self):
        """超过 3000 字符的输出截断"""
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "description": "查询", "output": "X" * 4000},
        }
        result = _format_step_outputs(step_results)
        assert "已截断" in result
        assert len(result) < 4000 + 200  # 截断后加上标记

    def test_strip_references_from_rag(self):
        """strip_references=True 时剥离参考文献"""
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "description": "检索知识",
                  "output": "答案内容\n\n---\n\n### 参考文献\n\n1. **doc1.md**"},
        }
        result = _format_step_outputs(step_results, strip_references=True)
        assert "参考文献已移至报告末尾" in result
        assert "doc1.md" not in result

    def test_strip_references_preserves_answer(self):
        """剥离参考文献时保留答案正文"""
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "description": "检索",
                  "output": "根据相关规定，叶菜类保鲜期为1-2天。\n\n---\n\n### 参考文献\n\n1. **手册.txt**"},
        }
        result = _format_step_outputs(step_results, strip_references=True)
        assert "叶菜类保鲜期为1-2天" in result
        assert "手册.txt" not in result


class TestExtractRagReferences:
    """从 RAG 输出中提取参考文献"""

    def test_extract_single_reference(self):
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "output": "答案\n\n---\n\n### 参考文献\n\n1. **生鲜手册.md**\n   - 类型: 制度规范"},
        }
        result = _extract_rag_references(step_results)
        assert "生鲜手册.md" in result

    def test_no_reference_section(self):
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "output": "这是纯文本回答，没有参考文献"},
        }
        result = _extract_rag_references(step_results)
        assert result == ""

    def test_skip_non_rag_steps(self):
        """跳过非 RAG 步骤"""
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "### 参考文献\n\n1. **should_not_appear.md**"},
        }
        result = _extract_rag_references(step_results)
        assert result == ""  # SQL 步骤的参考文献不提取

    def test_skip_failed_steps(self):
        step_results = {
            "1": {"capability": "search_knowledge", "status": "failed",
                  "output": "### 参考文献\n\n1. **failed_ref.md**"},
        }
        result = _extract_rag_references(step_results)
        assert result == ""

    def test_dedup_by_filename(self):
        """同文件名去重"""
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "output": "答案1\n\n---\n\n### 参考文献\n\n1. **doc.md**"},
            "2": {"capability": "search_knowledge", "status": "success",
                  "output": "答案2\n\n---\n\n### 参考文献\n\n1. **doc.md**"},
        }
        result = _extract_rag_references(step_results)
        # doc.md 只出现一次
        assert result.count("doc.md") == 1

    def test_alternative_marker(self):
        """"### 参考来源" 也能识别"""
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "output": "答案\n\n### 参考来源\n\n1. **手册.txt**"},
        }
        result = _extract_rag_references(step_results)
        assert "手册.txt" in result

    def test_skip_filtered_steps(self):
        """_filtered 标记的步骤跳过"""
        step_results = {
            "1": {"capability": "search_knowledge", "status": "success",
                  "_filtered": True,
                  "output": "### 参考文献\n\n1. **filtered_out.md**"},
        }
        result = _extract_rag_references(step_results)
        assert result == ""
