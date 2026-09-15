# -*- coding: utf-8 -*-
"""test_router_determinism.py — 路由确定性与复合意图回归测试（2026-09-15）

背景：同一问题跨重启在 plan/direct/workflow 间波动（LLM 兜底层温度非零、
向量索引重建后同分候选顺序漂移），且复合意图问题（SQL+RAG）没有确定性
plan 路径，plan 并行难以触发。

整改（企业路由器主流做法）：
  - 复合意图规则前置：显式连接词 + ≥2 能力组命中 → 稳定 PLAN（rule 层拍板）
  - LLM Router temperature=0（贪心解码）
  - Vector 同分候选按名字稳定排序
"""
import pytest

from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.types import ExecutionMode


@pytest.fixture(scope="module")
def router():
    return RuleRouter()


class TestCompositeIntent:
    def test_compound_sql_and_rag_goes_plan(self, router):
        """连接词 + SQL/RAG 双组命中 → plan，候选含两能力。"""
        d = router.route("查询库存不足的商品，并说明员工报销制度")
        assert d is not None
        assert d.execution_mode == ExecutionMode.PLAN
        names = {c.name for c in d.candidates}
        assert {"sql.query", "rag.search"} <= names
        assert d.confidence >= 0.8  # rule 层可直接拍板

    def test_compound_with_simultaneous(self, router):
        d = router.route("查询项目预算情况，同时从知识库查找项目管理经验")
        assert d is not None
        assert d.execution_mode == ExecutionMode.PLAN
        names = {c.name for c in d.candidates}
        assert {"sql.query", "rag.search"} <= names

    def test_single_intent_rag_not_torn_apart(self, router):
        """"退款审核时间是多少"是单意图问题：RAG 词命中但无连接词 → 不拆 plan。"""
        d = router.route("退款审核时间是多少？")
        if d is not None and d.execution_mode == ExecutionMode.PLAN:
            pytest.fail("单意图问题被误判为复合意图")
        # 期望：RAG direct 强信号（原行为保持），或 None 交下层
        if d is not None:
            assert d.candidates[0].name == "rag.search"

    def test_compound_without_second_group_falls_through(self, router):
        """有连接词但只命中单能力组 → 不判复合（防误杀），交原有逻辑。"""
        d = router.route("查询本月销售额，并统计订单数量")
        # 两组关键词都属 sql.query 组 → 不满足 ≥2 组 → 不应是 plan 拍板
        if d is not None:
            assert d.execution_mode != ExecutionMode.PLAN or d.confidence < 0.8

    def test_workflow_signal_still_wins(self, router):
        """workflow 强信号优先级高于复合意图（每天跑的定时任务不进 plan）。"""
        d = router.route("每天查询库存不足的商品并发送邮件提醒")
        assert d is not None
        assert d.execution_mode == ExecutionMode.WORKFLOW
