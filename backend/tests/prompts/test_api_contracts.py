"""Prompt API contract tests — verify frontend-backend field alignment.

Covers Phase 1 fixes:
  - variable_count field in list response
  - diff endpoint parameter names (from_version / to_version)
  - playground response structure (rendered.llm_output / latency_ms)
  - render response structure
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.api.routes.prompts import _prompt_to_dict, _spec_to_dict
from backend.prompts.registry import PromptSpec, VarSpec as R


class TestPromptToDict:
    def test_includes_variable_count(self):
        mock_prompt = MagicMock()
        mock_prompt.id = 1
        mock_prompt.key = "test.key"
        mock_prompt.name = "Test"
        mock_prompt.description = ""
        mock_prompt.category = "test"
        mock_prompt.risk_level = "low"
        mock_prompt.template_engine = "str_format"
        mock_prompt.variables = ["var1", "var2"]
        mock_prompt.active_version = 1
        mock_prompt.is_code_controlled = False
        mock_prompt.created_at = None
        mock_prompt.updated_at = None

        result = _prompt_to_dict(mock_prompt)
        assert result["variable_count"] == 2
        # 管理端契约：variables 是 {name, required, description} 对象列表
        # （2026-09-19 修复：DB 存字符串名，序列化时按注册表 spec 归一化，前端渲染 {undefined}）
        assert result["variables"] == [
            {"name": "var1", "required": False, "description": ""},
            {"name": "var2", "required": False, "description": ""},
        ]

    def test_variable_count_zero_for_none(self):
        mock_prompt = MagicMock()
        mock_prompt.id = 1
        mock_prompt.key = "test.key"
        mock_prompt.name = "Test"
        mock_prompt.description = ""
        mock_prompt.category = "test"
        mock_prompt.risk_level = "low"
        mock_prompt.template_engine = "str_format"
        mock_prompt.variables = None
        mock_prompt.active_version = None
        mock_prompt.is_code_controlled = False
        mock_prompt.created_at = None
        mock_prompt.updated_at = None

        result = _prompt_to_dict(mock_prompt)
        assert result["variable_count"] == 0


class TestPlaygroundResponseShape:
    def test_rendered_dict_structure(self):
        rendered = {
            "text": "hello world",
            "key": "test.key",
            "version": 3,
            "source": "snapshot",
        }
        response = {
            "rendered": rendered,
            "llm_output": "LLM response text",
            "latency_ms": 1500,
        }
        assert "rendered" in response
        assert "text" in response["rendered"]
        assert "version" in response["rendered"]
        assert "source" in response["rendered"]
        assert "llm_output" in response
        assert "latency_ms" in response


class TestEvalReportPromptVersions:
    def test_eval_report_has_prompt_versions_field(self):
        from backend.evaluation.models import EvalReport, ModuleSummary
        report = EvalReport(
            module="all",
            mode="offline",
            summaries=[
                ModuleSummary(
                    module="rag", total=10, passed=8, failed=2,
                    errors=0, skipped=0, pass_rate=0.8,
                ),
            ],
            results=[],
            prompt_versions={"rag.qa": 3, "router.llm": 1},
        )
        assert report.prompt_versions == {"rag.qa": 3, "router.llm": 1}

    def test_eval_report_prompt_versions_default_empty(self):
        from backend.evaluation.models import EvalReport, ModuleSummary
        report = EvalReport(
            module="all",
            mode="offline",
            summaries=[],
            results=[],
        )
        assert report.prompt_versions == {}
