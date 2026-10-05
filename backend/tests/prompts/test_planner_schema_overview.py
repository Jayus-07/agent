"""planner.system v2 事实变量化守卫测试（2026-10-06）。

锁定四件事：
  1. schema 概览从 SCHEMA_CONFIG 派生（表数一致、含样例表名，防回退手抄）；
  2. YAML 模板零 {{ 转义、无硬编码库表事实（防「15 张表」类漂移复发）；
  3. v1 DB 模板兼容：渲染调用多传变量不炸（renderer 按模板所需取值）；
  4. build_output_example 输出契约块可 format，且 PLANNER_SYSTEM（YAML 派生）
     能用调用方实参完整渲染。
"""
import pytest

from backend.prompts.loader import load_defaults
from backend.prompts.renderer import PromptRenderer


@pytest.fixture(scope="module")
def planner_template() -> str:
    return load_defaults()["planner.system"]


class TestSchemaOverviewDerivation:
    def test_table_count_matches_schema_config(self):
        from backend.agents.planner.planner import _format_schema_overview
        from backend.sql.data.schema_config import SCHEMA_CONFIG

        overview = _format_schema_overview()
        assert str(len(SCHEMA_CONFIG["tables"])) in overview
        assert "张表" in overview

    def test_contains_sample_table_and_schema_group(self):
        from backend.agents.planner.planner import _format_schema_overview
        from backend.sql.data.schema_config import SCHEMA_CONFIG

        overview = _format_schema_overview()
        some_full_name = next(iter(SCHEMA_CONFIG["tables"]))
        schema, _, table = some_full_name.partition(".")
        assert schema in overview
        assert table in overview

    def test_no_hardcoded_stale_facts(self, planner_template):
        # v1 曾硬编码「15 张表」；表数只能来自派生变量
        assert "15 张表" not in planner_template
        assert "{schema_overview}" in planner_template


class TestTemplateHygiene:
    def test_yaml_template_has_zero_brace_escapes(self, planner_template):
        # v1 有 29 处 {{ 转义；v2 输出示例外置后必须为 0
        assert "{{" not in planner_template
        assert "}}" not in planner_template

    def test_smoke_render_with_new_variables(self, planner_template):
        result = PromptRenderer.render(planner_template, {
            "schema_overview": "__SCHEMA__",
            "capabilities_schema": "__CAPS__",
            "output_example": "__EXAMPLE__",
        })
        assert "__SCHEMA__" in result
        assert "__CAPS__" in result
        assert "__EXAMPLE__" in result
        # 指令段保留（能力选择指南与规则仍归 YAML）
        assert "能力选择指南" in result
        assert "只输出 JSON，不要解释" in result


class TestV1Compat:
    def test_extra_variables_do_not_break_v1_template(self):
        # DB 现网 v1 模板只用 capabilities_schema/cap_example；
        # 渲染调用传全部 4 变量必须照常工作（renderer 按模板所需取值）
        v1_template = (
            "库表：\n{capabilities_schema}\n示例能力：{cap_example}"
        )
        result = PromptRenderer.render(v1_template, {
            "schema_overview": "__SCHEMA__",
            "capabilities_schema": "__CAPS__",
            "output_example": "__EXAMPLE__",
            "cap_example": "__CAPEX__",
        })
        assert "__CAPS__" in result and "__CAPEX__" in result
        assert "__SCHEMA__" not in result  # 未被 v1 模板引用的变量不落入文本


class TestOutputExample:
    def test_build_output_example_embeds_cap_example(self):
        from backend.prompts.planner import build_output_example

        text = build_output_example("sql.query")
        assert '"capability": "sql.query"' in text
        assert "__CAP_EXAMPLE__" not in text
        # 输出示例中的 JSON 大括号保持字面单括号（作为变量值注入，无需转义）
        assert '{"step_id": "1"' in text

    def test_planner_system_formats_with_caller_kwargs(self, planner_template):
        from backend.prompts.planner import PLANNER_SYSTEM

        # PLANNER_SYSTEM 从 YAML defaults 派生（G2：不维护第二份模板文本）
        assert PLANNER_SYSTEM == planner_template
        rendered = PLANNER_SYSTEM.format(
            schema_overview="S",
            capabilities_schema="C",
            output_example="E",
            cap_example="sql.query",  # str.format 忽略未引用的额外 kwargs
        )
        assert "S" in rendered and "C" in rendered and "E" in rendered
