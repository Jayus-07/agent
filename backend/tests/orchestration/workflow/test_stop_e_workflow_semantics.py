"""test_stop_e_workflow_semantics.py — STOP E：Daily Report / 其它 Workflow 失败语义

2026-10-07 全项目 Tool Failure Semantics 收口（任务 §九/§二十三）：
- E1：RAG 不可用但 SQL 数据在 → 报告照常生成，workflow=partial（非 success/failed）；
- E3：报告已生成落库后邮件失败 → 行状态 delivery_failed 保留报告，
  workflow=partial + 发送失败留痕；绝不把已生成报告当成不存在；
- E-optional：inventory_alert 无人消费/纯聚合 step 失败不炸全局。

只 mock Skill 边界（conftest patched_skill_adapter / patched_llm），不碰真实 DB。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch as mp

from backend.orchestration.workflow import workflow  # noqa: F401（注册装饰器自省）
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.skill_adapter import SkillStepFailure


def _run(registry, name: str, inputs: dict | None = None):
    return asyncio.run(WorkflowExecutor(registry=registry).run(name, inputs))


class TestDailyReportFailureSemantics:

    def test_daily_report_rag_failure_partial(self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm, monkeypatch):
        """E1：SQL 数据齐 + RAG 失败 → partial，报告与邮件照常完成"""
        from backend.orchestration.workflows.daily_report import DailyReport

        async def _rag_down(params):
            raise SkillStepFailure(
                "rag:rag.search 执行失败: 知识服务暂时不可用",
                step_result={"status": "failed", "tool_status": "unavailable",
                             "criticality": "important", "error_code": "connect_error"},
            )

        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_rag", _rag_down)
        # agent_analyze 消费 rules=""（RAG 缺失）：InventoryAnalyzer 走 patched_llm
        fresh_registry.register(DailyReport)

        ctx = _run(fresh_registry, "daily_report")
        assert ctx.status == "partial"
        assert "rag_query_template" in ctx.skip_steps
        assert ctx.step_failures["rag_query_template"]["tool_status"] == "unavailable"
        # 核心交付物未受牵连：报告生成 + 邮件发送两步都成功完成
        assert "generate_report" in ctx.outputs
        assert "send_email" in ctx.outputs

    def test_daily_report_email_failure_preserves_generated_report(self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm, monkeypatch):
        """E3：报告已生成落库 → 邮件失败 → 行=delivery_failed、workflow=partial、
        失败留痕；报告内容绝不消失"""
        from backend.orchestration.workflows.daily_report import DailyReport

        saved_rows: list[dict] = []

        class _FakeReportStore:
            def save(self, report: dict) -> str:
                saved_rows.append(dict(report))
                return report["id"]

        async def _email_down(params):
            raise SkillStepFailure(
                "email:email.send 执行失败: SMTP 服务不可用",
                step_result={"status": "failed", "tool_status": "unavailable",
                             "criticality": "important", "error_code": "connect_error"},
            )

        monkeypatch.setattr(
            "backend.orchestration.workflows.daily_report.call_email", _email_down)
        monkeypatch.setattr(
            "backend.seed.demo.runner.get_daily_report_store",
            lambda: _FakeReportStore(),
        )
        fresh_registry.register(DailyReport)

        ctx = _run(fresh_registry, "daily_report")
        assert ctx.status == "partial", "发送失败 ≠ 整个报告失败"
        assert "send_email" in ctx.skip_steps
        assert ctx.step_failures["send_email"]["error_code"] == "connect_error"
        # 行状态机：generated →（投递失败）delivery_failed；报告内容在库
        statuses = [row["status"] for row in saved_rows]
        assert "generated" in statuses
        assert statuses[-1] == "delivery_failed"
        report_row = saved_rows[-1]
        assert report_row["report_content"].strip(), "已生成的报告正文必须保留"
        # 报告生成 step 本身成功
        assert "generate_report" in ctx.outputs

    def test_daily_report_email_success_full_pipeline(self, fresh_registry, patched_trace_collector, patched_persistence, patched_skill_adapter, patched_llm, monkeypatch):
        """E4：全链成功 → 行状态 sent、workflow=success"""
        from backend.orchestration.workflows.daily_report import DailyReport

        saved_rows: list[dict] = []

        class _FakeReportStore:
            def save(self, report: dict) -> str:
                saved_rows.append(dict(report))
                return report["id"]

        monkeypatch.setattr(
            "backend.seed.demo.runner.get_daily_report_store",
            lambda: _FakeReportStore(),
        )
        fresh_registry.register(DailyReport)

        ctx = _run(fresh_registry, "daily_report")
        assert ctx.status == "success"
        assert saved_rows[-1]["status"] == "sent"


class TestInventoryAlertOptionalSteps:

    def test_optional_steps_declared_skip(self):
        """inventory_alert 的两个非核心 step 必须声明 on_error=skip——
        load_notification_policies（输出无人消费）/ create_event（纯聚合占位）。
        行为语义（skip→partial+留痕→继续）由 test_failure_semantics 的
        合成 workflow 用例覆盖，此处锁配置防回退。"""
        from backend.orchestration.workflows.inventory_alert import InventoryAlert

        assert InventoryAlert.load_notification_policies._step_config.on_error == "skip"
        assert InventoryAlert.create_event._step_config.on_error == "skip"
        # 核心交付物保持 fail-loud：邮件失败必须显式失败（不能假装成功）
        assert InventoryAlert.send_alert_email._step_config.on_error == "abort"

    def test_daily_report_step_policies(self):
        """daily_report 依赖矩阵锁型：fetch_sales/generate_report=abort（存在
        前提/核心交付物），fetch_inventory/fetch_promotions/rag/analyze/send_email
        =skip（缺节降级），send_email 零重试（写操作禁止盲重试）"""
        from backend.orchestration.workflows.daily_report import DailyReport

        assert DailyReport.fetch_sales._step_config.on_error == "abort"
        assert DailyReport.generate_report._step_config.on_error == "abort"
        assert DailyReport.fetch_inventory._step_config.on_error == "skip"
        assert DailyReport.fetch_promotions._step_config.on_error == "skip"
        assert DailyReport.rag_query_template._step_config.on_error == "skip"
        assert DailyReport.agent_analyze._step_config.on_error == "skip"
        send_cfg = DailyReport.send_email._step_config
        assert send_cfg.on_error == "skip"
        assert send_cfg.retry == 0
