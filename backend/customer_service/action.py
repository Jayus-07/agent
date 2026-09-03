"""customer_service/action.py — 业务操作数据结构

ActionType: 对齐 agent_actions 表的 CHECK 约束。
ActionProposal: Service 层产出的操作提案（展示给用户确认）。
AgentActionRecord: 对齐 agent_actions 表列，simulate_execute 产物。
ActionResult: 节点输出，放入 state.cs_action_result。
build_pending_action: 生成 pending_action dict（存入 ConfirmationStore）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.config.customer_service import CS_CONFIRMATION_TTL_SECONDS
from backend.customer_service.confirmation import (
    ConfirmationState,
    compute_expires_at,
)
from backend.customer_service.risk import RiskLevel


class ActionType:
    REFUND_REQUEST = "refund_request"
    RETURN_REQUEST = "return_request"
    EXCHANGE_REQUEST = "exchange_request"
    ADDRESS_UPDATE = "address_update"
    PASSWORD_RESET = "password_reset"
    ORDER_CANCEL = "order_cancel"
    REFUND_EXECUTE = "refund_execute"
    RETURN_EXECUTE = "return_execute"


@dataclass
class ActionProposal:
    action_type: str
    target_type: str  # "order" | "account" | "address"
    target_id: str
    risk_level: RiskLevel
    proposal_text: str
    before_state: dict = field(default_factory=dict)
    after_state: dict = field(default_factory=dict)
    requires_confirmation: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    def __post_init__(self):
        if self.expires_at is None and self.requires_confirmation:
            self.expires_at = compute_expires_at(
                self.created_at, CS_CONFIRMATION_TTL_SECONDS
            )


@dataclass
class AgentActionRecord:
    """对齐 agent_actions 表列。"""
    action_id: str
    action_type: str
    agent_type: str  # "ai" | "human"
    target_type: str
    target_id: str
    data: dict
    status: str  # "simulated" | "executed" | "failed"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: datetime | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "agent_type": self.agent_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "data": self.data,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "error_message": self.error_message,
        }


@dataclass
class ActionResult:
    """节点输出 — 放入 state.cs_action_result。"""
    action_type: str
    status: str  # "pending_confirmation" | "confirmed" | "executing" | "success" | "cancelled" | "expired" | "error"
    proposal_text: str = ""
    action_record: dict | None = None
    user_message: str = ""


def build_pending_action(proposal: ActionProposal) -> dict:
    """Generate the pending_action dict for ConfirmationStore.

    Structure matches design doc §7.5.
    """
    return {
        "action_id": str(uuid.uuid4()),
        "action_type": proposal.action_type,
        "target_type": proposal.target_type,
        "target_id": proposal.target_id,
        "risk_level": proposal.risk_level.value,
        "proposal_text": proposal.proposal_text,
        "before_state": proposal.before_state,
        "after_state": proposal.after_state,
        "requires_confirmation": proposal.requires_confirmation,
        "confirmation_state": ConfirmationState.PENDING_CONFIRMATION.value,
        "created_at": proposal.created_at.isoformat(),
        "expires_at": proposal.expires_at.isoformat() if proposal.expires_at else None,
    }
