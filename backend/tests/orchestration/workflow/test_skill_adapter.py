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
        """call_sql 的 query 模式走 execute_sql_tool 直连 PG（封套拆包）"""
        async def run():
            fake_tool = MagicMock()
            fake_tool.ainvoke = AsyncMock(
                return_value='{"status": "success", "data": {"rows": [], "total": 0}}')
            with mp("backend.orchestration.tools.execute_sql_tool", fake_tool):
                result = await call_sql({"query": "SELECT 1"})
                assert fake_tool.ainvoke.called
                assert result == {"rows": [], "total": 0}
        asyncio.run(run())

    def test_call_sql_failure_raises(self):
        """call_sql 的 query 模式：封套 status=failed 时上抛，不走业务数据路径"""
        async def run():
            fake_tool = MagicMock()
            fake_tool.ainvoke = AsyncMock(
                return_value='{"status": "failed", "error": "SQL 校验未通过"}')
            with mp("backend.orchestration.tools.execute_sql_tool", fake_tool):
                try:
                    await call_sql({"query": "DELETE FROM x"})
                    raised = False
                except ValueError as e:
                    raised = True
                    assert "SQL 校验未通过" in str(e)
                assert raised, "failed 封套应上抛 ValueError"
        asyncio.run(run())

    def test_call_sql_legacy_shape_passthrough(self):
        """call_sql 兼容：无 status 的历史形态原样透传（渐进迁移保护）"""
        async def run():
            fake_tool = MagicMock()
            fake_tool.ainvoke = AsyncMock(return_value='{"rows": [], "total": 0}')
            with mp("backend.orchestration.tools.execute_sql_tool", fake_tool):
                result = await call_sql({"query": "SELECT 1"})
                assert result == {"rows": [], "total": 0}
        asyncio.run(run())

    def test_call_sql_governed_records_tool_stats(self):
        """query 模式经 safe_tool_executor：record_tool_result 以契约键
        execute_sql_tool 记账（2026-10-01 键口径修正：旧口径记 capability
        名 sql.query，管理端按 lock 函数名合并永远匹配不上 → 行全 0）"""
        async def run():
            fake_tool = MagicMock()
            fake_tool.ainvoke = AsyncMock(
                return_value='{"status": "success", "data": {"rows": [1], "total": 1}}')
            recorded: list = []
            import backend.core.tool_runtime.executor as exec_mod
            with mp("backend.orchestration.tools.execute_sql_tool", fake_tool), \
                 mp.object(exec_mod, "record_tool_result",
                           lambda r, d="", tool_name="": recorded.append(
                               (tool_name or r.tool_name, r.status, d))):
                result = await call_sql({"query": "SELECT 1"})
                assert result == {"rows": [1], "total": 1}
            assert recorded, "治理路径必须落 record_tool_result"
            name, _status, domain = recorded[0]
            assert name == "execute_sql_tool", "指标键必须是 @tool 契约名（lock 键）"
            assert domain == "sql"
        asyncio.run(run())

    def test_call_sql_governed_failure_raises(self):
        """治理层失败（如连接异常）→ 上抛 ValueError 进 step 失败路径
        （与旧裸调行为等价：step 失败，只是消息来自治理层归一）"""
        async def _noop(_delay_ms):
            return None

        async def run():
            fake_tool = MagicMock()
            fake_tool.ainvoke = AsyncMock(side_effect=ConnectionError("PG 不可达"))
            import backend.core.tool_runtime.executor as exec_mod
            with mp("backend.orchestration.tools.execute_sql_tool", fake_tool), \
                 mp.object(exec_mod, "sleep_before_retry", _noop):
                try:
                    await call_sql({"query": "SELECT 1"})
                    raised = False
                except ValueError as e:
                    raised = True
                    assert "execute_sql_tool 失败" in str(e)
                assert raised, "治理层失败应上抛 ValueError"
        asyncio.run(run())

    def test_call_sql_question_mode(self):
        """call_sql 的 question 模式走 NL→SQL Agent（call_skill）"""
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"rows": [{"x": 1}]}),
            ) as mock:
                result = await call_sql({"question": "今天销售额"})
                assert mock.called
                assert result == {"rows": [{"x": 1}]}
        asyncio.run(run())

    def test_call_rag_passes_params(self):
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"answer": "模板"}),
            ) as mock:
                result = await call_rag({"question": "X", "kb_id": "ops"})
                assert mock.call_args.args == ("rag", "rag.search", {"question": "X", "kb_id": "ops"})
                assert result == {"answer": "模板"}
        asyncio.run(run())

    def test_call_rag_converts_query_to_question(self):
        """fix f10：旧调用方传 query 时兼容转为 question（RAGSkill 契约）。"""
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"answer": "模板"}),
            ) as mock:
                await call_rag({"query": "X", "kb_id": "ops"})
                assert mock.call_args.args == ("rag", "rag.search", {"question": "X", "kb_id": "ops"})
        asyncio.run(run())

    def test_call_rag_normalizes_str_output(self):
        """fix f14：RAGSkill output 为纯文本 str 时，归一为 {"answer": ...}，
        调用方 .get() 不再报 'str' object has no attribute 'get'。"""
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value="纯文本答案"),
            ):
                result = await call_rag({"question": "X"})
                assert result == {"answer": "纯文本答案"}
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

    def test_call_report_normalizes_str_output(self):
        """fix f16b：generate_report_tool 返回纯 Markdown str 时，归一为
        {"content": ...}，调用方 .get() 不再报 'str' object has no attribute 'get'。"""
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value="# 经营日报\n..."),
            ):
                result = await call_report({"report_type": "daily_sales"})
                assert result == {"content": "# 经营日报\n..."}
        asyncio.run(run())

    def test_call_email_passes_params(self):
        async def run():
            with mp(
                "backend.orchestration.workflow.skill_adapter.call_skill",
                AsyncMock(return_value={"sent": True}),
            ) as mock:
                result = await call_email({"to": ["a@b.c"]})
                # list 类型 to 会被转为 ; 分隔字符串
                assert mock.call_args.args == ("email", "email.send", {"to": "a@b.c"})
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