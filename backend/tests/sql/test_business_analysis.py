"""test_business_analysis.py — BusinessAnalysisSkill 端到端行为

覆盖：
  - 正常分析：SQLResult → BusinessInsight（summary/risks/suggestions/confidence）
  - 缺少前置输出 → failed（missing_dependency）
  - 前置输出为空 → failed（empty_input）
  - 前置输出格式错误 → failed（parse_error）
  - 多步骤 DAG 场景：sql.query → business.analyze → report.generate
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch as mp, MagicMock

import pytest

from backend.skills.business_analysis.skill import BusinessAnalysisSkill
from backend.skills.sql.models import SQLResult
from backend.skills.business_analysis.models import BusinessInsight


def _make_state(
    question: str = "哪些商品库存不足？",
    previous_outputs: dict | None = None,
    step_results: dict | None = None,
) -> dict:
    return {
        "question": question,
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "sql.query",
                    "description": "查询库存不足商品",
                },
                "2": {
                    "step_id": "2",
                    "capability": "business.analyze",
                    "description": "分析库存风险",
                },
            },
            "edges": {"2": ["1"]},
        },
        "current_step_id": "2",
        "previous_outputs": previous_outputs or {},
        "step_results": step_results or {},
        "current_user_id": None,
    }


def _make_sql_result() -> SQLResult:
    return SQLResult(
        sql="SELECT p.product_name, i.stock_quantity, i.safety_stock FROM product.products p JOIN inventory.inventory i ON i.product_id = p.id WHERE i.stock_quantity < i.safety_stock",
        tables=["product.products", "inventory.inventory"],
        columns=["product_name", "stock_quantity", "safety_stock"],
        rows=[
            {"product_name": "珍珠项链", "stock_quantity": 20, "safety_stock": 100},
            {"product_name": "银戒指", "stock_quantity": 80, "safety_stock": 100},
        ],
        row_count=2,
        execution_time=0.15,
    )


def _make_insight() -> BusinessInsight:
    return BusinessInsight(
        summary="珍珠项链和银戒指库存低于安全库存，存在缺货风险",
        risks=[
            "珍珠项链当前库存仅20件，安全库存100件，缺货风险高",
            "银戒指库存80件，接近安全库存线",
        ],
        suggestions=[
            "建议立即对珍珠项链补货至少200件",
            "建议对银戒指启动采购流程，补货50件",
        ],
        confidence=0.92,
        related_knowledge=["库存低于安全库存阈值时触发补货预警"],
    )


class TestBusinessAnalysisSuccess:
    def test_normal_analysis_flow(self):
        """正常流程：SQLResult → RAG 检索 → LLM 分析 → BusinessInsight"""
        async def run():
            state = _make_state(
                previous_outputs={"1": _make_sql_result().model_dump()},
            )
            # Mock RAG
            with mp(
                "backend.skills.business_analysis.skill.BusinessAnalysisSkill._fetch_rag_knowledge"
            ) as mock_rag:
                mock_rag.return_value = "库存低于安全库存阈值触发补货预警"
                # Mock analyzer
                with mp(
                    "backend.skills.business_analysis.analyzer.BusinessAnalyzer.analyze"
                ) as mock_analyze:
                    mock_analyze.return_value = _make_insight()
                    out = await BusinessAnalysisSkill().execute(state)

            sr = out["step_results"]["2"]
            assert sr["status"] == "success"
            output = sr["output"]
            assert isinstance(output, dict)
            assert "summary" in output
            assert "珍珠项链" in output["summary"]
            assert len(output.get("risks", [])) >= 1
            assert len(output.get("suggestions", [])) >= 1
            assert output.get("confidence", 0) > 0.8
        asyncio.run(run())

    def test_analysis_output_matches_insight_schema(self):
        """验证输出符合 BusinessInsight schema"""
        async def run():
            state = _make_state(
                previous_outputs={"1": _make_sql_result().model_dump()},
            )
            with mp(
                "backend.skills.business_analysis.skill.BusinessAnalysisSkill._fetch_rag_knowledge"
            ) as mock_rag:
                mock_rag.return_value = ""
                with mp(
                    "backend.skills.business_analysis.analyzer.BusinessAnalyzer.analyze"
                ) as mock_analyze:
                    mock_analyze.return_value = _make_insight()
                    out = await BusinessAnalysisSkill().execute(state)

            output = out["step_results"]["2"]["output"]
            # 验证可以反序列化为 BusinessInsight
            insight = BusinessInsight(**output)
            assert insight.summary
            assert 0.0 <= insight.confidence <= 1.0
        asyncio.run(run())


class TestBusinessAnalysisErrors:
    def test_missing_previous_outputs(self):
        """缺少前置 SQLResult → failed"""
        async def run():
            state = _make_state(previous_outputs={})
            out = await BusinessAnalysisSkill().execute(state)
            sr = out["step_results"]["2"]
            assert sr["status"] == "failed"
            assert sr["error_type"] == "missing_dependency"
            assert "缺少前置" in sr["error"]
        asyncio.run(run())

    def test_empty_previous_output(self):
        """前置步骤输出为 None → failed"""
        async def run():
            state = _make_state(previous_outputs={"1": None})
            out = await BusinessAnalysisSkill().execute(state)
            sr = out["step_results"]["2"]
            assert sr["status"] == "failed"
            assert sr["error_type"] == "empty_input"
        asyncio.run(run())

    def test_invalid_previous_output_format(self):
        """前置输出无法解析为 SQLResult → failed"""
        async def run():
            state = _make_state(previous_outputs={"1": {"not": "a sqlresult"}})
            out = await BusinessAnalysisSkill().execute(state)
            sr = out["step_results"]["2"]
            assert sr["status"] == "failed"
            assert sr["error_type"] == "parse_error"
        asyncio.run(run())

    def test_missing_current_step_id(self):
        """无 current_step_id → 返回空"""
        async def run():
            state = _make_state(
                previous_outputs={"1": _make_sql_result().model_dump()},
            )
            state["current_step_id"] = None
            out = await BusinessAnalysisSkill().execute(state)
            assert out == {}
        asyncio.run(run())


class TestBusinessAnalysisSkillContract:
    """对外契约验证"""

    def test_capability_declaration(self):
        skill = BusinessAnalysisSkill()
        assert skill.name == "business_analysis"
        assert "business.analyze" in skill.capabilities

    def test_description_not_empty(self):
        skill = BusinessAnalysisSkill()
        assert len(skill.description) > 10

    def test_step_results_preserves_other_steps(self):
        """不应清空已有 step_results"""
        async def run():
            state = _make_state(
                previous_outputs={"1": _make_sql_result().model_dump()},
                step_results={
                    "1": {
                        "step_id": "1",
                        "status": "success",
                        "capability": "sql.query",
                        "output": _make_sql_result().model_dump(),
                    },
                },
            )
            with mp(
                "backend.skills.business_analysis.skill.BusinessAnalysisSkill._fetch_rag_knowledge"
            ) as mock_rag:
                mock_rag.return_value = ""
                with mp(
                    "backend.skills.business_analysis.analyzer.BusinessAnalyzer.analyze"
                ) as mock_analyze:
                    mock_analyze.return_value = _make_insight()
                    out = await BusinessAnalysisSkill().execute(state)

            # step 1 保持不变
            assert "1" in out["step_results"]
            assert out["step_results"]["1"]["status"] == "success"
            # step 2 被添加
            assert "2" in out["step_results"]
            assert out["step_results"]["2"]["status"] == "success"
        asyncio.run(run())
