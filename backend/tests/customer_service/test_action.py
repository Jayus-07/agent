"""test_action.py — 业务操作数据结构测试"""
from datetime import datetime, timezone

from backend.customer_service.action import (
    ActionProposal,
    ActionType,
    AgentActionRecord,
    build_pending_action,
)
from backend.customer_service.risk import RiskLevel


class TestActionType:
    def test_refund_request(self):
        assert ActionType.REFUND_REQUEST == "refund_request"

    def test_return_request(self):
        assert ActionType.RETURN_REQUEST == "return_request"

    def test_exchange_request(self):
        assert ActionType.EXCHANGE_REQUEST == "exchange_request"

    def test_address_update(self):
        assert ActionType.ADDRESS_UPDATE == "address_update"

    def test_password_reset(self):
        assert ActionType.PASSWORD_RESET == "password_reset"


class TestActionProposal:
    def test_auto_expires_at_when_confirmation_required(self):
        proposal = ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="order",
            target_id="123",
            risk_level=RiskLevel.HIGH,
            proposal_text="test",
            requires_confirmation=True,
        )
        assert proposal.expires_at is not None
        assert proposal.expires_at > proposal.created_at

    def test_no_expires_at_when_no_confirmation(self):
        proposal = ActionProposal(
            action_type=ActionType.ADDRESS_UPDATE,
            target_type="account",
            target_id="u1",
            risk_level=RiskLevel.LOW,
            proposal_text="test",
            requires_confirmation=False,
        )
        assert proposal.expires_at is None

    def test_explicit_expires_at_not_overridden(self):
        explicit = datetime(2026, 12, 1, tzinfo=timezone.utc)
        proposal = ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="order",
            target_id="123",
            risk_level=RiskLevel.HIGH,
            proposal_text="test",
            expires_at=explicit,
        )
        assert proposal.expires_at == explicit


class TestAgentActionRecord:
    def test_to_dict(self):
        record = AgentActionRecord(
            action_id="a1",
            action_type=ActionType.REFUND_REQUEST,
            agent_type="ai",
            target_type="order",
            target_id="123",
            data={"simulated": True},
            status="simulated",
        )
        d = record.to_dict()
        assert d["action_id"] == "a1"
        assert d["action_type"] == "refund_request"
        assert d["status"] == "simulated"
        assert d["executed_at"] is None
        assert d["error_message"] is None

    def test_to_dict_with_executed_at(self):
        now = datetime.now(timezone.utc)
        record = AgentActionRecord(
            action_id="a2",
            action_type=ActionType.RETURN_REQUEST,
            agent_type="ai",
            target_type="order",
            target_id="456",
            data={},
            status="executed",
            executed_at=now,
        )
        d = record.to_dict()
        assert d["executed_at"] == now.isoformat()


class TestBuildPendingAction:
    def test_structure(self):
        proposal = ActionProposal(
            action_type=ActionType.REFUND_REQUEST,
            target_type="order",
            target_id="123",
            risk_level=RiskLevel.HIGH,
            proposal_text="refund text",
            before_state={"status": "paid"},
            after_state={"status": "refund_requested"},
        )
        pending = build_pending_action(proposal)

        assert "action_id" in pending
        assert pending["action_type"] == "refund_request"
        assert pending["target_type"] == "order"
        assert pending["target_id"] == "123"
        assert pending["risk_level"] == "high"
        assert pending["proposal_text"] == "refund text"
        assert pending["confirmation_state"] == "pending"
        assert pending["before_state"] == {"status": "paid"}
        assert pending["after_state"] == {"status": "refund_requested"}
        assert pending["expires_at"] is not None
