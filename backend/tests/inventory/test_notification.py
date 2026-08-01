"""test_notification.py — Notification Dispatcher 单元测试

覆盖决策 3：
1. 多 Policy OR 匹配（critical+手机 命中 2 个 policy）
2. 不同 alert_level 不命中
3. 不同 category 不命中
4. SILENT decision → None
5. 合并去重收件人
6. notify_on_upgrade 开关
7. notify_on_remind 开关
8. notify_on_resolve 开关
9. render_email_body 模板
"""
from __future__ import annotations

import pytest

from backend.orchestration.inventory import (
    InventoryStore,
    plan,
    render_email_body,
)
from backend.orchestration.inventory.state_machine import AlertDecision


class TestPolicyMatching:
    def test_default(self, fresh_store): pass  # placeholder
    """多维 OR 匹配（决策 3 选 C）"""

    def test_critical_default_matches_all(self, fresh_store):
        """critical_default（category=NULL）匹配所有 critical"""
        store = fresh_store
        store.save_policy({
            "policy_name": "critical_default",
            "alert_level": "critical",
            "notify_email": "ops@company.com",
        })
        matched = store.find_matching_policies("critical", "critical")
        assert len(matched) == 1
        assert matched[0]["policy_name"] == "critical_default"

    def test_critical_plus_phone_team_both_match(self, fresh_store):
        """critical+手机 → critical_default + phone_team 都命中"""
        store = fresh_store
        store.save_policy({
            "policy_name": "critical_default",
            "alert_level": "critical",
            "notify_email": "ops@company.com",
        })
        store.save_policy({
            "policy_name": "phone_team",
            "category": "手机",
            "alert_level": "critical",
            "notify_email": "phone-team@company.com",
        })
        matched = store.find_matching_policies("critical", "critical", "手机")
        assert len(matched) == 2
        names = {p["policy_name"] for p in matched}
        assert names == {"critical_default", "phone_team"}

    def test_different_level_no_match(self, fresh_store):
        """warning 不命中 critical policy"""
        store = fresh_store
        store.save_policy({
            "policy_name": "critical_default",
            "alert_level": "critical",
            "notify_email": "ops@company.com",
        })
        matched = store.find_matching_policies("warning", "low")
        assert matched == []

    def test_different_category_no_match(self, fresh_store):
        """critical+服装 不命中 critical+手机"""
        store = fresh_store
        store.save_policy({
            "policy_name": "phone_team",
            "category": "手机",
            "alert_level": "critical",
            "notify_email": "phone-team@company.com",
        })
        matched = store.find_matching_policies("critical", "critical", "服装")
        assert matched == []

    def test_disabled_policy_not_matched(self, fresh_store):
        """disabled=0 不参与匹配"""
        store = fresh_store
        store.save_policy({
            "policy_name": "disabled_one",
            "alert_level": "critical",
            "notify_email": "x@y.com",
            "enabled": 0,
        })
        matched = store.find_matching_policies("critical", "critical")
        assert matched == []  # 默认 enabled_only=True

    def test_semicolon_recipients_split(self, fresh_store):
        """多个收件人用 ; 分隔"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "a@x.com;b@y.com; c@z.com",
        })
        matched = store.find_matching_policies("critical", "critical")
        plan_result = plan(
            AlertDecision(action="UPGRADE", notify=True),
            "critical", "critical", None, store,
        )
        assert sorted(plan_result.recipients) == ["a@x.com", "b@y.com", "c@z.com"]
        assert len(plan_result.recipients) == 3


class TestPlan:
    """plan() 主决策逻辑"""

    def test_silent_returns_none(self, fresh_store):
        store = fresh_store
        decision = AlertDecision(action="SILENT", notify=False)
        assert plan(decision, "low", "warning", "手机", store) is None

    def test_no_matching_policy_returns_none(self, fresh_store):
        store = fresh_store
        decision = AlertDecision(action="UPGRADE", notify=True)
        # 无 policy
        assert plan(decision, "critical", "critical", "手机", store) is None

    def test_upgrade_default_notify(self, fresh_store):
        """UPGRADE 默认 notify_on_upgrade=1 → 通知"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
        })
        result = plan(
            AlertDecision(action="UPGRADE", notify=True),
            "critical", "critical", "手机", store,
        )
        assert result is not None
        assert "ops@x.com" in result.recipients
        assert result.action == "UPGRADE"

    def test_upgrade_disabled_skipped(self, fresh_store):
        """UPGRADE + notify_on_upgrade=0 → 跳过"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
            "notify_on_upgrade": 0,
        })
        result = plan(
            AlertDecision(action="UPGRADE", notify=True),
            "critical", "critical", "手机", store,
        )
        # 所有 policy 都关闭 → None
        assert result is None

    def test_remind_disabled_skipped(self, fresh_store):
        """REMIND + notify_on_remind=0 → 跳过"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
            "notify_on_remind": 0,
        })
        result = plan(
            AlertDecision(action="REMIND", notify=True, reason=["持续 critical 4h"]),
            "critical", "critical", "手机", store,
        )
        assert result is None

    def test_resolve_default_notify(self, fresh_store):
        """RESOLVE 默认 notify_on_resolve=1 → 通知恢复邮件"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "info",
            "notify_email": "ops@x.com",
        })
        result = plan(
            AlertDecision(action="RESOLVE", notify=True, reason=["库存恢复"]),
            "normal", "info", "手机", store,
        )
        assert result is not None
        assert result.action == "RESOLVE"

    def test_reopen_uses_upgrade_flag(self, fresh_store):
        """REOPEN 走 notify_on_upgrade（同 UPGRADE）"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
            "notify_on_upgrade": 0,  # 关闭 upgrade
        })
        result = plan(
            AlertDecision(action="REOPEN", notify=True, reason=["重开"]),
            "critical", "critical", "手机", store,
        )
        # upgrade 关闭 → REOPEN 也关闭
        assert result is None

    def test_create_uses_upgrade_flag(self, fresh_store):
        """CREATE 走 notify_on_upgrade（视为"创建告警"）"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
            "notify_on_upgrade": 0,
        })
        result = plan(
            AlertDecision(action="CREATE", notify=True, reason=["首次"]),
            "critical", "critical", "手机", store,
        )
        # upgrade 关闭 → CREATE 也关闭
        assert result is None

    def test_multiple_policies_dedupe_recipients(self, fresh_store):
        """多 Policy 合并去重收件人"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "a@x.com;b@x.com",
        })
        store.save_policy({
            "policy_name": "p2",
            "alert_level": "critical",
            "notify_email": "b@x.com;c@x.com",
        })
        result = plan(
            AlertDecision(action="UPGRADE", notify=True),
            "critical", "critical", "手机", store,
        )
        # b@x.com 出现两次，应去重
        assert sorted(result.recipients) == ["a@x.com", "b@x.com", "c@x.com"]
        # 两个 policy 都 matched
        assert len(result.matched_policies) == 2


class TestEmailBody:
    """render_email_body 邮件渲染"""

    def test_renders_create_email(self, fresh_store):
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
        })
        plan_result = plan(
            AlertDecision(action="CREATE", notify=True, reason=["首次"]),
            "critical", "critical", "手机", store,
        )
        subject, body = render_email_body(plan_result, extra={
            "product_id": "iPhone-15",
            "current_qty": 5,
            "daily_sales_avg": 0.5,
            "stock_days": 10,
            "case_id": 100,
            "detected_at": "2026-07-31T10:00:00",
        })
        assert "iPhone-15" in subject
        assert "iPhone-15" in body
        assert "100" in body  # case_id
        assert "5" in body  # current_qty

    def test_renders_resolve_email_subject(self, fresh_store):
        """RESOLVE 邮件 subject 应包含 ✅"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "info",
            "notify_email": "ops@x.com",
        })
        plan_result = plan(
            AlertDecision(action="RESOLVE", notify=True, reason=["库存恢复"]),
            "normal", "info", "手机", store,
        )
        subject, _ = render_email_body(plan_result, extra={"product_id": "X"})
        assert "恢复" in subject or "RESOLVE" in subject.upper()

    def test_renders_upgrade_email(self, fresh_store):
        """UPGRADE 邮件"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
        })
        plan_result = plan(
            AlertDecision(action="UPGRADE", notify=True, reason=["low→critical"]),
            "critical", "critical", "手机", store,
        )
        subject, body = render_email_body(plan_result, extra={"product_id": "X"})
        assert "升级" in subject
        assert "low→critical" in body

    def test_renders_reminder_email(self, fresh_store):
        """REMIND 邮件"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
        })
        plan_result = plan(
            AlertDecision(action="REMIND", notify=True, reason=["持续 critical 4h"]),
            "critical", "critical", "手机", store,
        )
        subject, body = render_email_body(plan_result, extra={"product_id": "X"})
        assert "提醒" in subject
        assert "4" in body  # 4h

    def test_renders_reopen_email(self, fresh_store):
        """REOPEN 邮件"""
        store = fresh_store
        store.save_policy({
            "policy_name": "p1",
            "alert_level": "critical",
            "notify_email": "ops@x.com",
        })
        plan_result = plan(
            AlertDecision(action="REOPEN", notify=True, reason=["重开"]),
            "critical", "critical", "手机", store,
        )
        subject, _ = render_email_body(plan_result, extra={"product_id": "X"})
        assert "重开" in subject

    def test_renders_none_plan(self, fresh_store):
        """plan=None → 占位 subject + body"""
        subject, body = render_email_body(None, extra={"product_id": "X"})
        assert "关闭" in subject or "占位" in subject
        assert "X" in body