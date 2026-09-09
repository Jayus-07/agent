"""test_refund_service.py — 退款服务测试"""
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from backend.customer_service.action import ActionProposal, ActionType
from backend.customer_service.errors import (
    OrderNotEligibleError,
    OrderNotFoundError,
)
from backend.customer_service.risk import RiskLevel
from backend.customer_service.service.refund_service import (
    RefundService,
)


@dataclass
class FakeSQLResult:
    rows: list
    status: str = "success"
    error: str = ""
    row_count: int = 0


def _order_row(
    order_id="1", order_no="ORD-001", customer_id="u1",
    total_amount=299.0, status="paid",
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


class TestCheckRefundEligibility:
    @patch.object(RefundService, "_has_existing_refund", return_value=False)
    @patch.object(RefundService, "_get_order")
    def test_eligible_order(self, mock_get_order, mock_refund):
        mock_get_order.return_value = _order_row(status="paid")
        svc = RefundService()
        result = svc.check_refund_eligibility("u1", "1")
        assert result.eligible is True
        assert result.amount == 299.0
        assert result.status == "paid"

    @patch.object(RefundService, "_get_order")
    def test_non_refundable_status(self, mock_get_order):
        mock_get_order.return_value = _order_row(status="pending")
        svc = RefundService()
        with pytest.raises(OrderNotEligibleError, match="不允许退款"):
            svc.check_refund_eligibility("u1", "1")

    @patch.object(RefundService, "_has_existing_refund", return_value=True)
    @patch.object(RefundService, "_get_order")
    def test_duplicate_refund_rejected(self, mock_get_order, mock_refund):
        mock_get_order.return_value = _order_row(status="paid")
        svc = RefundService()
        with pytest.raises(OrderNotEligibleError, match="已存在退款记录"):
            svc.check_refund_eligibility("u1", "1")

    @patch.object(RefundService, "_get_order")
    def test_order_not_found(self, mock_get_order):
        mock_get_order.side_effect = OrderNotFoundError()
        svc = RefundService()
        with pytest.raises(OrderNotFoundError):
            svc.check_refund_eligibility("u1", "999")

    @pytest.mark.parametrize("status", ["paid", "shipped", "completed"])
    @patch.object(RefundService, "_has_existing_refund", return_value=False)
    @patch.object(RefundService, "_get_order")
    def test_all_refundable_statuses(self, mock_get_order, mock_refund, status):
        mock_get_order.return_value = _order_row(status=status)
        svc = RefundService()
        result = svc.check_refund_eligibility("u1", "1")
        assert result.eligible is True


class TestBuildRefundProposal:
    @patch.object(RefundService, "_has_existing_refund", return_value=False)
    @patch.object(RefundService, "_get_order")
    def test_proposal_structure(self, mock_get_order, mock_refund):
        mock_get_order.return_value = _order_row(
            order_id="42", order_no="ORD-042", total_amount=599.0, status="shipped",
        )
        svc = RefundService()
        proposal = svc.build_refund_proposal("u1", "42", reason="质量问题")

        assert isinstance(proposal, ActionProposal)
        assert proposal.action_type == ActionType.REFUND_REQUEST
        assert proposal.target_type == "order"
        assert proposal.target_id == "42"
        assert proposal.risk_level == RiskLevel.HIGH
        assert "ORD-042" in proposal.proposal_text
        assert "599.00" in proposal.proposal_text
        assert "质量问题" in proposal.proposal_text
        assert proposal.before_state["status"] == "shipped"
        assert proposal.after_state["status"] == "refund_requested"


class TestSimulateExecute:
    def test_returns_simulated_record(self):
        svc = RefundService()
        proposal = ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="order",
            target_id="42",
            risk_level=RiskLevel.HIGH,
            proposal_text="test",
            before_state={"status": "paid"},
            after_state={"status": "refund_requested"},
        )
        record = svc.simulate_execute(proposal)
        assert record.status == "simulated"
        assert record.action_type == "refund_request"
        assert record.target_id == "42"
        assert record.data["simulated"] is True
        assert record.executed_at is not None
