"""test_skill_adapter.py — Skill 适配器

覆盖：
- call_sql / call_rag / call_report / call_email 各调一次
- _build_state 契约（与 Planner state 兼容）
- 未知 skill 抛 ValueError
- 不污染 module-level state
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest

from backend.orchestration.workflow.skill_adapter import (
    _build_state,
    call_email,
    call_rag,
    call_report,
    call_skill,
    call_sql,
)


# ─────────────────────────────────────────────────────────────
# _build_state 契约
# ─────────────────────────────────────────────────────────────

class TestBuildStateContract:
    """_build_state 构造的 state 与 Planner state 兼容"""

    def test_state_has_required_keys(self):
        """state 必须包含 current_step_id / plan / step_results"""
        state = _build_state("sql", "sql.query", {"q": 1})
        assert "current_step_id" in state
        assert "plan" in state
        assert "step_results" in state

    def test_state_plan_contains_capability_and_params(self):
        """plan.nodes[step_id] 含 capability + params"""
        state = _build_state("sql", "sql.query", {"q": "SELECT 1"})
        step = state["plan"]["nodes"]["sql"]
        assert step["capability"] == "sql.query"
        assert step["params"] == {"q": "SELECT 1"}

    def test_state_uses_skill_name_as_step_id(self):
        """step_id 是 skill_name（不是 step 名）"""
        state = _build_state("rag", "rag.search", {})
        assert state["current_step_id"] == "rag"

    def test_state_fresh_each_call(self):
        """每次调用创建新 dict（不引用 module-level state）"""
        s1 = _build_state("sql", "sql.query", {"q": 1})
        s2 = _build_state("sql", "sql.query", {"q": 2})
        # 不是同一对象
        assert s1 is not s2
        assert s1["plan"]["nodes"]["sql"]["params"]["q"] == 1
        assert s2["plan"]["nodes"]["sql"]["params"]["q"] == 2
        # 修改 s1 不影响 s2
        s1["plan"]["nodes"]["sql"]["params"]["q"] = 999
        assert s2["plan"]["nodes"]["sql"]["params"]["q"] == 2


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

class TestSkillCalls:
    """call_sql / call_rag / call_report / call_email"""

    def test_call_sql_passes_params(self):
        """call_sql 把 params 传给 call_skill"""
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"rows": []}),
            ) as mock:
                result = await call_sql({"query": "SELECT 1"})
                assert mock.called
                call_args = mock.call_args
                assert call_args.args == ("sql", "sql.query", {"query": "SELECT 1"})
                assert result == {"rows": []}
        asyncio.run(run())

    def test_call_rag_passes_params(self):
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"answer": "模板"}),
            ) as mock:
                result = await call_rag({"query": "X", "kb_id": "ops"})
                assert mock.call_args.args == ("rag", "rag.search", {"query": "X", "kb_id": "ops"})
                assert result == {"answer": "模板"}
        asyncio.run(run())

    def test_call_report_passes_params(self):
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"content": "报告"}),
            ) as mock:
                result = await call_report({"template": "daily"})
                assert mock.call_args.args == ("report", "report.generate", {"template": "daily"})
                assert result == {"content": "报告"}
        asyncio.run(run())

    def test_call_email_passes_params(self):
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"sent": True}),
            ) as mock:
                result = await call_email({"to": ["a@b.c"]})
                assert mock.call_args.args == ("email", "email.send", {"to": ["a@b.c"]})
                assert result == {"sent": True}
        asyncio.run(run())


# ─────────────────────────────────────────────────────────────
# 未知 skill
# ─────────────────────────────────────────────────────────────

class TestUnknownSkill:
    """未知 skill_name 抛 ValueError"""

    def test_call_skill_with_unknown_skill_raises(self):
        async def run():
            with pytest.raises(ValueError, match="Unknown skill"):
                await call_skill("nonexistent_skill", "cap", {})
        asyncio.run(run())

    def test_call_skill_lists_supported_skills_in_error(self):
        async def run():
            try:
                await call_skill("xyz", "cap", {})
                assert False
            except ValueError as e:
                msg = str(e)
                # 错误信息列出支持的 skill
                assert "sql" in msg
                assert "rag" in msg
                assert "email" in msg
        asyncio.run(run())