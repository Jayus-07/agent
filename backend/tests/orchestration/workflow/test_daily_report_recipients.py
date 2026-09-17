"""test_daily_report_recipients.py — 日报邮件收件人可配置（B6 发送侧验收配套）

背景（B6 欠账：批次 1 发送侧真实验收）：
daily_report Step 7 send_email 原收件人写死 ops@demo.local / ceo@demo.local，
无法注入真实收件人做投递验收。改造为 REPORT_EMAIL_TO 环境变量可配置，
未配置回落 demo 地址（既有冒烟测试与 demo 行为不变）。

覆盖：
- _report_email_recipients 纯函数：未配置/单地址/多地址/脏值回落
- workflow 集成：REPORT_EMAIL_TO 注入后 call_email 收到配置的收件人
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration.workflows.daily_report import (
    _DEFAULT_REPORT_EMAIL_TO,
    _report_email_recipients,
)


# ─────────────────────────────────────────────────────────────
# 纯函数：收件人解析
# ─────────────────────────────────────────────────────────────

class TestReportEmailRecipients:
    """_report_email_recipients 环境变量解析"""

    def test_unset_falls_back_to_demo(self, monkeypatch):
        """未配置 REPORT_EMAIL_TO → 回落 demo 默认（行为不变）"""
        monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
        assert _report_email_recipients() == _DEFAULT_REPORT_EMAIL_TO

    def test_single_address(self, monkeypatch):
        """单地址：原样返回"""
        monkeypatch.setenv("REPORT_EMAIL_TO", "mint1614@agent.qq.com")
        assert _report_email_recipients() == "mint1614@agent.qq.com"

    def test_multiple_addresses_normalized(self, monkeypatch):
        """多地址（含空格）：逗号分隔解析并规范化为 ', ' join"""
        monkeypatch.setenv(
            "REPORT_EMAIL_TO", " a@x.com , b@y.com,  c@z.com ")
        assert _report_email_recipients() == "a@x.com, b@y.com, c@z.com"

    def test_dirty_value_falls_back_to_demo(self, monkeypatch):
        """纯逗号/空格等脏值 → 视同未配置，回落 demo 默认"""
        monkeypatch.setenv("REPORT_EMAIL_TO", " , , ")
        assert _report_email_recipients() == _DEFAULT_REPORT_EMAIL_TO


# ─────────────────────────────────────────────────────────────
# 集成：workflow send_email step 使用配置的收件人
# ─────────────────────────────────────────────────────────────

class TestWorkflowUsesConfiguredRecipients:
    """REPORT_EMAIL_TO 注入后，send_email step 的 call_email 参数随之变化"""

    def test_send_email_uses_env_recipients(
        self, fresh_registry, patched_trace_collector, patched_persistence,
        patched_skill_adapter, patched_llm, monkeypatch,
    ):
        from backend.orchestration.workflow.executor import WorkflowExecutor
        from backend.orchestration.workflows.daily_report import DailyReport

        monkeypatch.setenv("REPORT_EMAIL_TO", "mint1614@agent.qq.com")
        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx = asyncio.run(executor.run("daily_report"))

        assert ctx.status == "success", f"Workflow failed: {ctx.error}"
        assert "send_email" in ctx.outputs
        patched_skill_adapter["call_email"].assert_called_once()
        params = patched_skill_adapter["call_email"].call_args[0][0]
        assert params["to"] == ["mint1614@agent.qq.com"]
        assert params["subject"].startswith("[经营日报]")

    def test_send_email_default_recipients_without_env(
        self, fresh_registry, patched_trace_collector, patched_persistence,
        patched_skill_adapter, patched_llm, monkeypatch,
    ):
        """未配置 env → 维持 demo 默认收件人（回归保护）"""
        from backend.orchestration.workflow.executor import WorkflowExecutor
        from backend.orchestration.workflows.daily_report import DailyReport

        monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        ctx = asyncio.run(executor.run("daily_report"))

        assert "send_email" in ctx.outputs
        params = patched_skill_adapter["call_email"].call_args[0][0]
        assert params["to"] == [a.strip() for a in _DEFAULT_REPORT_EMAIL_TO.split(",")]

    @pytest.mark.parametrize("addr", ["a@x.com", "b@y.com"])
    def test_each_recipient_reaches_call_email(
        self, fresh_registry, patched_trace_collector, patched_persistence,
        patched_skill_adapter, patched_llm, monkeypatch, addr,
    ):
        """多收件人逐个进入 call_email 的 to 列表"""
        from backend.orchestration.workflow.executor import WorkflowExecutor
        from backend.orchestration.workflows.daily_report import DailyReport

        monkeypatch.setenv("REPORT_EMAIL_TO", f"{addr}, extra@z.com")
        fresh_registry.register(DailyReport)
        executor = WorkflowExecutor(registry=fresh_registry)
        asyncio.run(executor.run("daily_report"))

        params = patched_skill_adapter["call_email"].call_args[0][0]
        assert addr in params["to"]
