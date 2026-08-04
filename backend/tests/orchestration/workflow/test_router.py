"""test_router.py — TaskRouter 三层加权评分

覆盖：
- 加权评分公式正确性（0.3/0.2/0.5）
- 阈值分层（>=0.3 workflow / >=0.15 LLM / <0.15 agent）
- 无 Index 兜底
- 中文 metadata 匹配
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration.workflow import workflow, step
from backend.orchestration.workflow.router import TaskRouter, RouteResult
from backend.orchestration.workflow.registry import WorkflowRegistry


# ─────────────────────────────────────────────────────────────
# 评分公式
# ─────────────────────────────────────────────────────────────

class TestRouterScoring:
    """三层加权评分"""

    def test_score_formula_exact(self, fresh_registry):
        """验证公式: 0.3*object + 0.2*action + 0.5*workflow_match"""
        @workflow(
            name="test_score",
            objects=["a", "b"],        # 2 个对象
            actions=["x"],            # 1 个动作
            examples=["示例"],
            default_kbs=[],
        )
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        fresh_registry.build_router_index()
        router = TaskRouter(registry=fresh_registry)

        # query="a" → object=1/2=0.5, action=0, match=0.5
        # total = 0.3*0.5 + 0.2*0 + 0.5*0.5 = 0.15 + 0 + 0.25 = 0.4
        async def run():
            return await router.route("a")
        result = asyncio.run(run())
        assert abs(result.confidence - 0.4) < 0.001

    def test_score_action_only(self, fresh_registry):
        """只有 action 匹配"""
        @workflow(
            name="test_action",
            objects=[],
            actions=["生成", "导出"],
            examples=[],
            default_kbs=[],
        )
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        fresh_registry.build_router_index()
        router = TaskRouter(registry=fresh_registry)

        # query="生成" → action=1/2=0.5, object=0, match=0.5
        # total = 0.3*0 + 0.2*0.5 + 0.5*0.5 = 0.35
        async def run():
            return await router.route("生成")
        result = asyncio.run(run())
        assert abs(result.confidence - 0.35) < 0.001

    def test_workflow_match_weighted_heaviest(self, fresh_registry):
        """workflow_match 权重最大"""
        # 同分条件下，workflow_match 加权应该比 object 加权更优先
        # object=0.5, action=0, match=0.5
        # total1 = 0.3*0.5 + 0.2*0 + 0.5*0.5 = 0.4
        # total2（object=1.0, action=0, match=0）
        # = 0.3*1.0 + 0 + 0 = 0.3
        @workflow(
            name="test_wm",
            objects=["x"],
            actions=[],
            examples=[],
            default_kbs=[],
        )
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        fresh_registry.build_router_index()
        router = TaskRouter(registry=fresh_registry)

        async def run():
            return await router.route("x")
        result = asyncio.run(run())
        # match=1.0 (x 既是 object 又是 match)，score=0.3+0.5=0.8
        assert abs(result.confidence - 0.8) < 0.001


# ─────────────────────────────────────────────────────────────
# 阈值分层
# ─────────────────────────────────────────────────────────────

class TestRouterThresholds:
    """score → candidate_mode 决策"""

    def test_high_score_routes_to_workflow(self, fresh_registry):
        """score >= 0.3 → workflow"""
        @workflow(
            name="hi",
            objects=["x"],
            actions=["x"],
            examples=["x"],
            default_kbs=[],
        )
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        fresh_registry.build_router_index()
        router = TaskRouter(registry=fresh_registry)

        async def run():
            return await router.route("x")
        result = asyncio.run(run())
        assert result.is_workflow
        assert result.workflow_candidate == "hi"

    def test_low_score_routes_to_agent(self, fresh_registry):
        """score < 0.15 → agent"""
        @workflow(
            name="lo",
            objects=["a", "b", "c"],
            actions=["x", "y", "z"],
            examples=["e"],
            default_kbs=[],
        )
        class TWF:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(TWF)
        fresh_registry.build_router_index()
        router = TaskRouter(registry=fresh_registry)

        # query "完全无关" → object=0, action=0, match=0 → score=0
        async def run():
            return await router.route("完全无关词")
        result = asyncio.run(run())
        assert result.is_agent
        assert result.workflow_candidate is None

    def test_no_index_routes_to_agent(self, fresh_registry):
        """无 Index 时默认 agent"""
        # fresh_registry 不注册任何 workflow
        router = TaskRouter(registry=fresh_registry)
        async def run():
            return await router.route("anything")
        result = asyncio.run(run())
        assert result.is_agent
        assert result.confidence <= 0.5  # confidence 不应高


# ─────────────────────────────────────────────────────────────
# 真实场景
# ─────────────────────────────────────────────────────────────

class TestRouterRealScenarios:
    """业务场景路由"""

    @pytest.fixture
    def two_workflows(self, fresh_registry):
        """注册 daily_report + inventory_alert"""
        @workflow(
            name="daily_report",
            objects=["日报", "销售", "运营"],
            actions=["生成", "发送"],
            examples=["生成今天的经营日报", "跑日报"],
            default_kbs=["analytics"],
        )
        class DR:
            @step()
            async def s(self, ctx): return {}

        @workflow(
            name="inventory_alert",
            objects=["库存", "补货"],
            actions=["检查", "预警"],
            examples=["检查库存风险"],
            default_kbs=["policies"],
        )
        class IA:
            @step()
            async def s(self, ctx): return {}

        fresh_registry.register(DR)
        fresh_registry.register(IA)
        fresh_registry.build_router_index()
        return fresh_registry

    def test_chinese_routes_daily_report(self, two_workflows):
        """中文 query 路由 daily_report"""
        router = TaskRouter(registry=two_workflows)
        async def run():
            return await router.route("帮我生成今天的经营日报")
        result = asyncio.run(run())
        assert result.is_workflow
        assert result.workflow_candidate == "daily_report"

    def test_chinese_routes_inventory_alert(self, two_workflows):
        """中文 query 路由 inventory_alert"""
        router = TaskRouter(registry=two_workflows)
        async def run():
            return await router.route("检查库存风险")
        result = asyncio.run(run())
        assert result.is_workflow
        assert result.workflow_candidate == "inventory_alert"

    def test_unrelated_routes_to_agent(self, two_workflows):
        """无关 query 路由 agent"""
        router = TaskRouter(registry=two_workflows)
        async def run():
            return await router.route("股票")
        result = asyncio.run(run())
        assert result.is_agent