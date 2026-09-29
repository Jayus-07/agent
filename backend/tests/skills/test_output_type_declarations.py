# -*- coding: utf-8 -*-
"""tests/skills/test_output_type_declarations.py — STOP G M5 契约守卫

锁两件事（docs/architecture/STOP_G_Preparation_Audit.md §5.1 M1/M5）：
  1. 全部 12 个 Skill 必须显式声明 output_type（text|structured），禁止隐式默认
     ——隐式声明会让「Tool 实际返回封套但边界按 text 放行」的漂移静默发生
     （map.lookup 裸 JSON 透出缺陷即由此而来）；
  2. 声明 ≡ Tool 实际输出形状：Tool 模块源码使用统一封套出口
     （tool_success_result / tool_error_result / map._base ok|fail|not_configured）
     ⇒ structured；否则 ⇒ text。

G2 说明：本测试是「声明 vs 实际」的防漂移门，检测逻辑从代码派生；
重写 execute 的 Skill（sql / business_analysis）不经 Tool 边界，走 allowlist
并单独断言其 structured 声明。
"""
from __future__ import annotations

import inspect
import re
import sys

import pytest

from backend.skills.registry import _instances

# 重写 execute()、不经 BaseSkill Tool 边界的 Skill（output 契约在 execute 内自洽）
EXECUTE_OVERRIDE_SKILLS = {"sql", "business_analysis"}

# 统一封套出口的源码特征（tools/map/_base.py 与 shared/tool_envelope.py 两族）
_ENVELOPE_MARKERS = re.compile(
    r"tool_success_result|tool_error_result|not_configured"
    r"|from backend\.tools\.map\._base import|from backend\.shared\.tool_envelope import"
)

SKILL_NAMES = {"rag", "report", "email", "data_export", "web_search",
               "web_crawl", "data_collection", "business_analysis",
               "competitor_analysis", "travel_poi", "sql", "map_lookup"}


def _all_skills():
    return {s.name: s for s in _instances}


class TestExplicitDeclarations:
    """M1：禁止隐式默认——每个 Skill 类必须自带 output_type 声明。"""

    def test_all_twelve_skills_registered(self):
        assert set(_all_skills()) == SKILL_NAMES

    @pytest.mark.parametrize("skill_name", sorted(SKILL_NAMES))
    def test_output_type_declared_on_class(self, skill_name):
        skill = _all_skills()[skill_name]
        # vars(类) 只含本类显式定义的属性——继承自 BaseSkill 的默认不算声明
        assert "output_type" in vars(type(skill)), (
            f"{skill_name} 未显式声明 output_type（隐式依赖基类默认值）"
        )
        assert type(skill).output_type in ("text", "structured"), (
            f"{skill_name} output_type 非法: {type(skill).output_type!r}"
        )

    def test_execute_override_skills_declare_structured(self):
        for name in EXECUTE_OVERRIDE_SKILLS:
            skill = _all_skills()[name]
            assert type(skill).output_type == "structured", (
                f"{name} 重写 execute 消费结构化协议，必须声明 structured"
            )


class TestDeclarationMatchesToolShape:
    """M5：声明 ≡ Tool 实际输出形状（防漂移门）。"""

    def _tool_output_kind(self, skill) -> str:
        tool = skill._tool_fn
        func = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
        module_name = func.__module__ if func is not None else tool.__module__
        source = inspect.getsource(sys.modules[module_name])
        # 模块级检测：map 聚合 Tool 的 ok/fail 调用分布在同模块 action 处理函数里，
        # 函数级源码看不见；同模块混两种风格（如 sql.py）不属于任何 skill 的 _tool_fn
        if _ENVELOPE_MARKERS.search(source):
            return "structured"
        return "text"

    @pytest.mark.parametrize("skill_name", sorted(SKILL_NAMES - EXECUTE_OVERRIDE_SKILLS))
    def test_declared_kind_matches_tool_shape(self, skill_name):
        skill = _all_skills()[skill_name]
        declared = type(skill).output_type
        detected = self._tool_output_kind(skill)
        assert declared == detected, (
            f"{skill_name}: 声明 output_type={declared!r} 但其 Tool 模块实际"
            f"输出形状是 {detected!r}（封套出口 {'存在' if detected == 'structured' else '不存在'}）"
        )
