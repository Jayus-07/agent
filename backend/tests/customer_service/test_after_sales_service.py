"""test_after_sales_service.py — 售后服务测试"""
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from backend.customer_service.action import ActionProposal, ActionType
from backend.customer_service.errors import (
    OrderNotEligibleError,
)
from backend.customer_service.risk import RiskLevel
from backend.customer_service.service.after_sales_service import (
    AfterSalesService,
)


@dataclass
class FakeSQLResult:
    rows: list
    status: str = "success"
    error: str = ""


def _order_row(
    order_id="1", order_no="ORD-001", customer_id="u1",
    total_amount=299.0, status="shipped",
):
    return {
        "id": order_id,
        "order_no": order_no,
        "customer_id": customer_id,
        "total_amount": total_amount,
        "status": status,
        "payment_status": "paid",
        "created_at": "2026-09-01",
    }


class TestReturnEligibility:
    @patch.object(AfterSalesService, "_get_order")
    def test_eligible_shipped(self, mock_get):
        mock_get.return_value = _order_row(status="shipped")
        svc = AfterSalesService()
        result = svc.check_return_eligibility("u1", "1")
        assert result.eligible is True

    @patch.object(AfterSalesService, "_get_order")
    def test_eligible_completed(self, mock_get):
        mock_get.return_value = _order_row(status="completed")
        svc = AfterSalesService()
        result = svc.check_return_eligibility("u1", "1")
        assert result.eligible is True

    @patch.object(AfterSalesService, "_get_order")
    def test_ineligible_paid(self, mock_get):
        mock_get.return_value = _order_row(status="paid")
        svc = AfterSalesService()
        with pytest.raises(OrderNotEligibleError, match="不允许退货"):
            svc.check_return_eligibility("u1", "1")


class TestExchangeEligibility:
    @patch.object(AfterSalesService, "_get_order")
    def test_eligible_shipped(self, mock_get):
        mock_get.return_value = _order_row(status="shipped")
        svc = AfterSalesService()
        result = svc.check_exchange_eligibility("u1", "1")
        assert result.eligible is True

    @patch.object(AfterSalesService, "_get_order")
    def test_ineligible_pending(self, mock_get):
        mock_get.return_value = _order_row(status="pending")
        svc = AfterSalesService()
        with pytest.raises(OrderNotEligibleError, match="不允许换货"):
            svc.check_exchange_eligibility("u1", "1")


class TestBuildProposals:
    @patch.object(AfterSalesService, "_get_order")
    def test_return_proposal(self, mock_get):
        mock_get.return_value = _order_row(
            order_id="10", order_no="ORD-010", total_amount=199.0, status="shipped",
        )
        svc = AfterSalesService()
        proposal = svc.build_return_proposal("u1", "10", reason="尺码不对")

        assert proposal.action_type == ActionType.RETURN_REQUEST
        assert proposal.risk_level == RiskLevel.HIGH
        assert "ORD-010" in proposal.proposal_text
        assert "尺码不对" in proposal.proposal_text
        assert proposal.after_state["status"] == "return_requested"

    @patch.object(AfterSalesService, "_get_order")
    def test_exchange_proposal(self, mock_get):
        mock_get.return_value = _order_row(
            order_id="10", order_no="ORD-010", total_amount=199.0, status="completed",
        )
        svc = AfterSalesService()
        proposal = svc.build_exchange_proposal("u1", "10")

        assert proposal.action_type == ActionType.EXCHANGE_REQUEST
        assert proposal.risk_level == RiskLevel.MEDIUM
        assert "ORD-010" in proposal.proposal_text
        assert proposal.after_state["status"] == "exchange_requested"


class TestSimulateExecute:
    def test_simulated_record(self):
        svc = AfterSalesService()
        proposal = ActionProposal(
            action_type=ActionType.RETURN_REQUEST,
            target_type="order",
            target_id="10",
            risk_level=RiskLevel.HIGH,
            proposal_text="test",
        )
        record = svc.simulate_execute(proposal)
        assert record.status == "simulated"
        assert record.action_type == "return_request"
