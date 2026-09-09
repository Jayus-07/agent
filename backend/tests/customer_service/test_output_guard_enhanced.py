"""test_output_guard_enhanced.py — Output Guard Phase 5 增强过滤测试"""
import pytest

from backend.customer_service.security.output_guard import CSOutputGuard


@pytest.fixture
def guard():
    return CSOutputGuard()


class TestHandoffInternalFiltering:

    def test_handoff_state_enum_filtered(self, guard):
        result = guard.check("当前状态为 ai_active 请继续")
        assert result.filtered
        assert "handoff_state_leak" in result.reasons
        assert "ai_active" not in result.text

    def test_handoff_requested_filtered(self, guard):
        result = guard.check("状态已变为 handoff_requested")
        assert result.filtered
        assert "handoff_state_leak" in result.reasons

    def test_handoff_ticket_id_filtered(self, guard):
        result = guard.check("您的转接工单号是 HANDOFF-AB12CD34")
        assert result.filtered
        assert "handoff_ticket_id" in result.reasons
        assert "HANDOFF-AB12CD34" not in result.text

    def test_handoff_enum_reference_filtered(self, guard):
        result = guard.check("状态为 HandoffState.AI_ACTIVE")
        assert result.filtered
        assert "handoff_enum_leak" in result.reasons

    def test_handoff_internal_field_filtered(self, guard):
        result = guard.check('handoff_state: "requested"')
        assert result.filtered
        assert "handoff_internal_field" in result.reasons


class TestComplaintInternalFiltering:

    def test_complaint_ticket_id_filtered(self, guard):
        result = guard.check("投诉工单 COMPLAINT-XY987654 已创建")
        assert result.filtered
        assert "complaint_ticket_id" in result.reasons
        assert "COMPLAINT-XY987654" not in result.text

    def test_severity_leak_filtered(self, guard):
        result = guard.check('severity: high 已评估')
        assert result.filtered
        assert "complaint_severity_leak" in result.reasons

    def test_matched_patterns_leak_filtered(self, guard):
        result = guard.check('matched_patterns: ["pattern1"]')
        assert result.filtered
        assert "complaint_pattern_leak" in result.reasons


class TestCleanTextPassesThrough:

    def test_normal_response_not_filtered(self, guard):
        result = guard.check("您的订单已发货，预计明天到达。")
        assert not result.filtered
        assert result.text == "您的订单已发货，预计明天到达。"

    def test_handoff_user_facing_text_clean(self, guard):
        result = guard.check("已为您转接人工客服，请稍候。")
        assert not result.filtered


class TestCombinedFiltering:

    def test_internal_and_handoff_filtered(self, guard):
        result = guard.check(
            "SELECT * FROM orders 当前状态 ai_active HANDOFF-12345678"
        )
        assert result.filtered
        assert "sql_statement" in result.reasons
        assert "handoff_state_leak" in result.reasons
        assert "handoff_ticket_id" in result.reasons
