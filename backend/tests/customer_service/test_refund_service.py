"""test_refund_service.py — 退款服务测试"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.customer_service.action import ActionProposal, ActionType
from backend.customer_service.errors import (
    OrderNotEligibleError,
    OrderNotFoundError,
    RefundRequestUnavailableError,
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
        # 相对时间：退款窗口按下单时间判定，写死日期的夹具会随时间失效
        "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
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
        assert "不会即时退款" in proposal.proposal_text
        assert "3-5 个工作日" not in proposal.proposal_text
        assert proposal.before_state["status"] == "shipped"
        assert proposal.after_state["status"] == "refund_requested"
        assert proposal.after_state["reason"] == "质量问题"


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


def _refund_pending(amount="299.00", status="paid", policy_version="v1"):
    return {
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": "42",
        "before_state": {
            "order_id": "42", "status": status, "amount": amount,
            "policy_version": policy_version,
        },
        "after_state": {"status": "refund_requested", "reason": "质量问题"},
    }


class TestExecuteRefundRequest:
    def test_only_explicit_sandbox_registers_a_pending_case(
        self, monkeypatch,
    ):
        from backend.config import customer_service as cfg
        from backend.customer_service.service.refund_service import RefundEligibility

        monkeypatch.setattr(cfg, "CS_REFUND_REQUEST_MODE", "sandbox")
        monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
        service = RefundService()
        monkeypatch.setattr(
            service,
            "check_refund_eligibility",
            lambda *_: RefundEligibility(
                True, "42", "ORD-042", 299.0, "paid", policy_version="v1",
            ),
        )
        case_service = type("CaseService", (), {
            "create_refund_request_sync": lambda _self, **fields: {
                "request_id": fields["request_id"],
                "status": "pending_review",
            },
        })()
        monkeypatch.setattr(
            "backend.customer_service.case.service.get_case_service",
            lambda: case_service,
        )

        record = service.execute_refund_request(
            _refund_pending(),
            confirmation_id="d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
            user_id="user-1",
            tenant_id="tenant-1",
            conversation_id="conversation-1",
        )

        assert record.status == "executed"
        assert record.action_id == "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11"
        assert record.data["request_status"] == "pending_review"
        assert record.data["funds_refunded"] is False

    @pytest.mark.parametrize(
        ("request_mode", "gateway_mode"),
        [("disabled", "sandbox"), ("sandbox", "http")],
    )
    def test_unavailable_mode_fails_before_any_order_or_case_write(
        self, monkeypatch, request_mode, gateway_mode,
    ):
        from backend.config import customer_service as cfg

        monkeypatch.setattr(cfg, "CS_REFUND_REQUEST_MODE", request_mode)
        monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", gateway_mode)
        service = RefundService()
        monkeypatch.setattr(service, "check_refund_eligibility", lambda *_: pytest.fail())

        with pytest.raises(RefundRequestUnavailableError):
            service.execute_refund_request(
                _refund_pending(),
                confirmation_id="d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
                user_id="user-1",
                tenant_id="tenant-1",
                conversation_id="conversation-1",
            )

    def test_changed_proposal_facts_are_rejected_before_case_write(self, monkeypatch):
        from backend.config import customer_service as cfg
        from backend.customer_service.service.refund_service import RefundEligibility

        monkeypatch.setattr(cfg, "CS_REFUND_REQUEST_MODE", "sandbox")
        monkeypatch.setattr(cfg, "CS_BUSINESS_GATEWAY_MODE", "sandbox")
        service = RefundService()
        monkeypatch.setattr(
            service,
            "check_refund_eligibility",
            lambda *_: RefundEligibility(
                True, "42", "ORD-042", 300.0, "paid", policy_version="v1",
            ),
        )
        with patch(
            "backend.customer_service.case.service.get_case_service",
        ) as get_case_service:
            with pytest.raises(OrderNotEligibleError, match="已变化"):
                service.execute_refund_request(
                    _refund_pending(),
                    confirmation_id="d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
                    user_id="user-1",
                    tenant_id="tenant-1",
                    conversation_id="conversation-1",
                )
            get_case_service.assert_not_called()
