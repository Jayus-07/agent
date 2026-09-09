"""test_risk.py — 风险等级评估测试"""
import pytest

from backend.customer_service.risk import (
    RiskLevel,
    assess_action_risk,
    requires_confirmation,
    requires_human_review,
)


class TestAssessActionRisk:
    def test_default_low(self):
        assert assess_action_risk() == RiskLevel.LOW

    def test_critical_action_overrides(self):
        assert assess_action_risk(
            base_risk="medium", action_type="account_delete"
        ) == RiskLevel.CRITICAL

    def test_high_risk_action_escalates(self):
        assert assess_action_risk(
            base_risk="medium", action_type="refund_execute"
        ) == RiskLevel.HIGH

    def test_high_risk_action_no_downgrade(self):
        assert assess_action_risk(
            base_risk="critical", action_type="refund_execute"
        ) == RiskLevel.CRITICAL

    def test_large_amount_escalates_to_critical(self):
        assert assess_action_risk(
            base_risk="medium", action_type="refund_request", amount=1500.0
        ) == RiskLevel.CRITICAL

    def test_small_amount_no_escalation(self):
        assert assess_action_risk(
            base_risk="medium", action_type="refund_request", amount=299.0
        ) == RiskLevel.MEDIUM

    def test_invalid_base_risk_defaults_to_low(self):
        assert assess_action_risk(base_risk="bogus") == RiskLevel.LOW

    def test_risk_level_enum_input(self):
        assert assess_action_risk(
            base_risk=RiskLevel.HIGH, action_type=""
        ) == RiskLevel.HIGH


class TestRequiresConfirmation:
    def test_low_no_confirmation(self):
        assert requires_confirmation(RiskLevel.LOW) is False

    @pytest.mark.parametrize("risk", [
        RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL,
    ])
    def test_non_low_requires_confirmation(self, risk):
        assert requires_confirmation(risk) is True


class TestRequiresHumanReview:
    def test_critical_requires_review(self):
        assert requires_human_review(RiskLevel.CRITICAL) is True

    @pytest.mark.parametrize("risk", [
        RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH,
    ])
    def test_non_critical_no_review(self, risk):
        assert requires_human_review(risk) is False
