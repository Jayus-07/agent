# -*- coding: utf-8 -*-
"""BaseSkill 输出契约测试。

execute() 在 Tool 返回边界按 output_type / output_types 声明归一化输出：
  - text（默认）: 非 str（如 dict）必须序列化为字符串——防止 SQLResult dict
    透传进 final_answer 炸掉下游（done 事件 sources、记忆落库等）
  - structured: 非 dict 时尽力从 JSON 解析，失败保持原样并告警
"""
import asyncio
import json

from backend.skills.base import BaseSkill, _CompatSkill


def _state(step_id="step_1"):
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {"capability": "dummy.cap",
                                     "description": "测试步骤",
                                     "params": {}}},
                 "edges": {}},
    }


def _run(tool_fn):
    return asyncio.run(_CompatSkill(tool_fn).execute(_state(), step_capability="dummy.cap"))


class _DictTool:
    def invoke(self, params):
        return {"columns": ["x"], "rows": [{"x": 1}]}


class _StrTool:
    def invoke(self, params):
        return "正常文本输出"


class _JsonStrTool:
    def invoke(self, params):
        return json.dumps({"summary": "洞察"}, ensure_ascii=False)


class TestTextContract:
    """默认 output_type=text：输出必须是 str。"""

    def test_str_passthrough(self):
        out = _run(_StrTool())
        assert out["step_results"]["step_1"]["output"] == "正常文本输出"

    def test_dict_serialized_to_json(self):
        """曾发生的事故：dict 原样透传进 final_answer。"""
        out = _run(_DictTool())
        output = out["step_results"]["step_1"]["output"]
        assert isinstance(output, str)
        assert json.loads(output) == {"columns": ["x"], "rows": [{"x": 1}]}


class _StructuredCompat(_CompatSkill):
    output_type = "structured"


class TestStructuredContract:
    """output_type=structured：输出必须是 dict。"""

    def _run_structured(self, tool_fn):
        skill = _StructuredCompat(tool_fn)
        return asyncio.run(skill.execute(_state(), step_capability="dummy.cap"))

    def test_dict_passthrough(self):
        out = self._run_structured(_DictTool())
        assert out["step_results"]["step_1"]["output"] == {
            "columns": ["x"], "rows": [{"x": 1}]}

    def test_json_str_parsed_to_dict(self):
        out = self._run_structured(_JsonStrTool())
        assert out["step_results"]["step_1"]["output"] == {"summary": "洞察"}

    def test_non_json_str_kept_as_is(self):
        """非 JSON 字符串不销毁数据，保持原样（告警由日志承载）。"""
        out = self._run_structured(_StrTool())
        assert out["step_results"]["step_1"]["output"] == "正常文本输出"


class TestPerCapabilityOverride:
    """output_types 按 capability 覆盖 output_type。"""

    def test_capability_level_override(self):
        class _Mixed(_CompatSkill):
            output_type = "text"
            output_types = {"dummy.cap": "structured"}

        skill = _Mixed(_DictTool())
        out = asyncio.run(skill.execute(_state(), step_capability="dummy.cap"))
        assert out["step_results"]["step_1"]["output"] == {
            "columns": ["x"], "rows": [{"x": 1}]}


class TestDeclaratedSkills:
    """重写 execute 的两个 skill 应声明 structured 契约（文档价值）。"""

    def test_sql_skill_declares_structured(self):
        from backend.skills.sql.skill import SQLSkill
        assert SQLSkill.output_type == "structured"

    def test_business_analysis_declares_structured(self):
        from backend.skills.business_analysis.skill import BusinessAnalysisSkill
        assert BusinessAnalysisSkill.output_type == "structured"
