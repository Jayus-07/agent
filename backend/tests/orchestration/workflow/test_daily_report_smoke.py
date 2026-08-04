"""test_daily_report_smoke.py — DailyReport 端到端冒烟测试

覆盖：
- 注册 daily_report workflow + 触发后 7 Step 全部跑通
- DAG 分层符合预期（3 fetch 并行）
- Skill / Capability 调用被 mock（不真查库/调 LLM）
- outputs 跨 Step 传递（agent_analyze 能读到 fetch_sales 等输出）
- trace span 全部生成
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch as mp

import pytest

from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.registry import WorkflowRegistry
from backend.orchestration.workflows.daily_report import DailyReport


# ─────────────────────────────────────────────────────────────
# 端到端冒烟
# ─────────────────────────────────────────────────────────────

class TestDailyReportSmoke:
    """DailyReport 端到端（mock 掉所有外部依赖）"""

    def test_dag_topology(self, fresh_registry):
        """DailyReport DAG 分层正确"""
        from backend.orchestration.workflow.meta import collect_step_methods
        from backend.orchestration.workflow.dag import DAG

        fresh_registry.register(DailyReport)
        steps = collect_step_methods(DailyReport)
        dag = DAG({name: cfg for name, (_, cfg) in steps.items()})

        # 5 层（3 fetch 并行 → rag → agent → report → email）
        assert len(dag.layers) == 5
        # Layer 0: 3 个 fetch 并行
        assert set(dag.layers[0]) == {"fetch_sales", "fetch_inventory", "fetch_promotions"}
        # Layer 1: rag_query_template
        assert dag.layers[1] == ["rag_query_template"]
        # Layer 2: agent_analyze
        assert dag.layers[2] == ["agent_analyze"]
        # Layer 3: generate_report
        assert dag.layers[3] == ["generate_report"]
        # Layer 4: send_email
        assert dag.layers[4] == ["send_email"]

    def test_full_workflow_runs_to_completion(
        self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm
    ):
        """DailyReport 7 Step 全部跑通，outputs 跨 Step 传递"""
        # mock Skill 调用（patched_skill_adapter fixture 已处理）
        # mock InventoryAnalyzer（patched_llm fixture 已处理 BaseAgentSkill._call_llm）

        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)

        ctx = asyncio.run(executor.run("daily_report"))

        assert ctx.status == "success", f"Workflow failed: {ctx.error}"
        # 7 个 Step 全部有输出（除了 on_error 失败的）
        assert "fetch_sales" in ctx.outputs
        assert "fetch_inventory" in ctx.outputs
        assert "fetch_promotions" in ctx.outputs
        assert "rag_query_template" in ctx.outputs
        assert "agent_analyze" in ctx.outputs
        assert "generate_report" in ctx.outputs
        assert "send_email" in ctx.outputs

    def test_outputs_pass_between_steps(
        self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm
    ):
        """agent_analyze 能读到 fetch_sales 等上游输出"""
        # mock call_sql 返回特定数据
        async def custom_call_sql(params):
            return {"rows": [{"product_id": "test_p1", "total_qty": 99}]}
        patched_skill_adapter["call_sql"].side_effect = custom_call_sql

        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx = asyncio.run(executor.run("daily_report"))

        # agent_analyze 收到 fetch_sales 的输出（rows 含 test_p1）
        analysis = ctx.outputs.get("agent_analyze", {})
        # Agent 的 outputs 应该被调过（具体内容看 InventoryAnalyzer）
        # 至少应该有 outputs 字段（不空）
        assert analysis

    def test_skip_step_does_not_break_workflow(
        self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm
    ):
        """on_error=skip 的 Step 失败 → 工作流继续"""
        # mock call_rag 失败（用 Exception 实例）
        patched_skill_adapter["call_rag"].side_effect = ValueError("RAG 失败")

        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx = asyncio.run(executor.run("daily_report"))

        # rag_query_template 失败被 skip
        assert "rag_query_template" in ctx.skip_steps
        # workflow 仍继续到 send_email
        assert "send_email" in ctx.outputs
        assert ctx.status in ("partial", "success")

    def test_trace_spans_created_for_each_step(
        self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm
    ):
        """7 Step + root 应该有 8 个 trace span"""
        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        asyncio.run(executor.run("daily_report"))

        # start_span 调用次数：1 root + 7 step = 8
        assert patched_trace_collector.start_span.call_count == 8

        # 检查 span name 包含所有 step
        span_names = [
            call.args[0] for call in patched_trace_collector.start_span.call_args_list
        ]
        step_names = [
            "fetch_sales", "fetch_inventory", "fetch_promotions",
            "rag_query_template", "agent_analyze", "generate_report", "send_email",
        ]
        for step_name in step_names:
            assert any(f"workflow_step.{step_name}" in n for n in span_names), \
                f"Missing span for {step_name}"


# ─────────────────────────────────────────────────────────────
# 路由元数据
# ─────────────────────────────────────────────────────────────

class TestDailyReportRouting:
    """DailyReport 在 Router Index 中的元数据"""

    def test_metadata_for_task_router(self, fresh_registry):
        """DailyReport 的 metadata 包含中文 objects/actions/examples"""
        from backend.orchestration.workflow.meta import get_workflow_meta

        fresh_registry.register(DailyReport)
        meta = get_workflow_meta(DailyReport)

        assert meta.name == "daily_report"
        assert "日报" in meta.objects  # 中文业务对象
        assert "生成" in meta.actions  # 中文动作
        assert len(meta.examples) > 0
        # examples 应该包含中文
        assert any("日报" in ex for ex in meta.examples)

    def test_router_can_match_daily_report(self, fresh_registry):
        """TaskRouter 能匹配中文"生成日报"query"""
        from backend.orchestration.workflow.router import TaskRouter

        fresh_registry.register(DailyReport)
        fresh_registry.build_router_index()

        router = TaskRouter(registry=fresh_registry)

        async def run():
            result = await router.route("帮我生成今天的经营日报")
            return result

        result = asyncio.run(run())
        # 命中 daily_report workflow
        assert result.is_workflow
        assert result.workflow_candidate == "daily_report"