"""test_sql_skill_structured.py — SQL Skill 重构后端到端行为

适配 v2 架构：
  - output 从 Markdown 字符串改为 Pydantic SQLResult dict
  - 移除手动 Trace 断言（Trace 由 TraceMiddleware 统一管理）

覆盖：
  - schema 不匹配（表不存在）→ status="failed"
  - router 找不到表 → status="failed", error_type="no_table"
  - validation 失败 → status="failed", error_type="validation_error"
  - 成功 → status="success", output 含 SQLResult dict
  - no_data → status="success", is_empty=True
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch as mp

import pytest

from backend.skills.sql.skill import SQLSkill
from backend.sql.sql_result import SQLResult


def _make_state(question: str = "查询最近一个月内价格最高的商品信息") -> dict:
    return {
        "question": question,
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "sql.query",
                    "description": question,
                }
            }
        },
        "current_step_id": "1",
        "step_results": {},
        "current_user_id": None,
    }


def _sr(result: dict) -> dict:
    return result["step_results"]["1"]


# ─────────────────────────────────────────────────────────────
# SQLSkill 直接驱动：mock SQLAgent.ask_struct
# ─────────────────────────────────────────────────────────────

class TestSQLSkillSuccess:
    def test_success_populates_status_and_row_count(self):
        async def run():
            fake = SQLResult.success(
                rows=[{"sku": "A", "price": 99.0}],
                columns=["sku", "price"],
                sql="SELECT * FROM product.products",
                elapsed=0.1,
            )
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            sr = _sr(out)
            assert sr["status"] == "success"
            assert sr["row_count"] == 1
            assert sr["is_empty"] is False
            assert sr["error"] is None
            # v2: output 是 Pydantic SQLResult dict
            assert isinstance(sr["output"], dict)
            assert sr["output"]["rows"] == [{"sku": "A", "price": 99.0}]
            assert sr["output"]["columns"] == ["sku", "price"]
            assert sr["output"]["row_count"] == 1
            assert sr["output"]["execution_time"] == 0.1
        asyncio.run(run())

    def test_no_data_is_success_with_is_empty(self):
        async def run():
            fake = SQLResult.success(rows=[], columns=["sku"], sql="SELECT 1")
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            sr = _sr(out)
            assert sr["status"] == "success"
            assert sr["is_empty"] is True
            assert sr["row_count"] == 0
            assert sr["output"]["rows"] == []
            assert sr["output"]["row_count"] == 0
        asyncio.run(run())


class TestSQLSkillErrorMapping:
    """SQLResult.status → StepResult.status / error_type 映射。"""

    def _check(self, sql_status: str, expected_step_status: str, expected_error_type: str):
        async def run():
            fake = SQLResult.failed(
                status=sql_status,
                error=f"mock {sql_status}",
                error_type="mock",
            )
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            sr = _sr(out)
            assert sr["status"] == expected_step_status, f"{sql_status} → {expected_step_status}"
            assert sr["error_type"] == expected_error_type
            assert sr["error"] == f"mock {sql_status}"
        asyncio.run(run())

    def test_no_table(self):
        self._check("no_table", "failed", "no_table")

    def test_syntax_error(self):
        self._check("syntax_error", "failed", "syntax_error")

    def test_permission_denied(self):
        self._check("permission_denied", "failed", "permission_denied")

    def test_validation_error(self):
        self._check("validation_error", "failed", "validation_error")

    def test_timeout(self):
        self._check("timeout", "failed", "timeout")

    def test_failed_generic(self):
        async def run():
            fake = SQLResult.failed(status="failed", error="mock failed", error_type="mock")
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            sr = _sr(out)
            assert sr["status"] == "failed"
            assert sr["error"] == "mock failed"
            assert sr["error_type"] == "mock"
        asyncio.run(run())


class TestSQLSkillRetryPolicy:
    """syntax / permission / validation / no_table 不应触发重试。"""

    def test_syntax_error_does_not_retry(self):
        async def run():
            attempts = []

            def fake_ask_struct(*a, **k):
                attempts.append(1)
                return SQLResult.failed(status="syntax_error", error="relation x does not exist")

            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = fake_ask_struct
                out = await SQLSkill().execute(_make_state())
            sr = _sr(out)
            assert sr["status"] == "failed"
            assert sr["error_type"] == "syntax_error"
            assert len(attempts) == 1, f"expected 1 attempt, got {len(attempts)}"
        asyncio.run(run())

    def test_permission_denied_does_not_retry(self):
        async def run():
            attempts = []

            def fake_ask_struct(*a, **k):
                attempts.append(1)
                return SQLResult.failed(status="permission_denied", error="权限不足")

            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = fake_ask_struct
                out = await SQLSkill().execute(_make_state())
            assert _sr(out)["status"] == "failed"
            assert _sr(out)["error_type"] == "permission_denied"
            assert len(attempts) == 1
        asyncio.run(run())


class TestSQLSkillContract:
    """对外契约：返回 dict with step_results；output 为 Pydantic SQLResult dict。"""

    def test_returns_step_results_dict(self):
        async def run():
            fake = SQLResult.success(rows=[{"x": 1}], columns=["x"], sql="SELECT 1")
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            assert "step_results" in out
            assert "1" in out["step_results"]
        asyncio.run(run())

    def test_output_is_sqlresult_dict(self):
        """v2: output 是 SQLResult.model_dump() dict，含必须字段。"""
        async def run():
            fake = SQLResult.success(
                rows=[{"x": 1}], columns=["x"], sql="SELECT 1", elapsed=0.05
            )
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(_make_state())
            output = _sr(out)["output"]
            assert isinstance(output, dict)
            assert "sql" in output
            assert "columns" in output
            assert "rows" in output
            assert "row_count" in output
            assert "execution_time" in output
            assert output["execution_time"] == 0.05
        asyncio.run(run())

    def test_missing_current_step_id_returns_empty(self):
        async def run():
            state = _make_state()
            state["current_step_id"] = None
            out = await SQLSkill().execute(state)
            assert out == {}
        asyncio.run(run())

    def test_step_results_preserves_other_steps(self):
        """不应清空已有 step_results 中其他 step 的数据。"""
        async def run():
            fake = SQLResult.success(rows=[{"x": 1}], columns=["x"], sql="SELECT 1")
            state = _make_state()
            state["step_results"] = {
                "0": {"step_id": "0", "status": "success", "output": "old"},
            }
            with mp("backend.skills.sql.skill.get_sql_agent") as get_agent:
                get_agent.return_value.ask_struct = lambda *a, **k: fake
                out = await SQLSkill().execute(state)
            assert "0" in out["step_results"]
            assert out["step_results"]["0"]["status"] == "success"
            assert "1" in out["step_results"]
        asyncio.run(run())
