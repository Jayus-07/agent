"""customer_service/risk.py — 风险等级评估

四级风险:
  LOW      — 无需确认 (如查询)
  MEDIUM   — 需要确认 (如地址修改)
  HIGH     — 需要确认 + 审计 (如退款、退货)
  CRITICAL — 双重确认 + 人工审核 (如批量退款、删号)

设计参考: docs/customer-service/design.md §15.1
"""
from __future__ import annotations

from enum import Enum

from backend.config.customer_service import (
    CS_CRITICAL_ACTIONS,
    CS_CRITICAL_REFUND_AMOUNT,
    CS_HIGH_RISK_ACTIONS,
)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _parse_base_risk(base_risk: str | RiskLevel) -> RiskLevel:
    if isinstance(base_risk, RiskLevel):
        return base_risk
    try:
        return RiskLevel(base_risk)
    except ValueError:
        return RiskLevel.LOW


def assess_action_risk(
    base_risk: str | RiskLevel = "low",
    action_type: str = "",
    amount: float | None = None,
    order_status: str | None = None,
) -> RiskLevel:
    """Determine the effective risk level for an action.

    The base_risk comes from the intent profile (intents.py).
    Escalation rules:
      - action_type in CS_CRITICAL_ACTIONS → CRITICAL
      - action_type in CS_HIGH_RISK_ACTIONS → at least HIGH
      - amount >= CS_CRITICAL_REFUND_AMOUNT → at least CRITICAL
    """
    risk = _parse_base_risk(base_risk)

    if action_type in CS_CRITICAL_ACTIONS:
        return RiskLevel.CRITICAL

    if action_type in CS_HIGH_RISK_ACTIONS:
        if _RISK_ORDER[risk] < _RISK_ORDER[RiskLevel.HIGH]:
            risk = RiskLevel.HIGH

    if amount is not None and amount >= CS_CRITICAL_REFUND_AMOUNT:
        if _RISK_ORDER[risk] < _RISK_ORDER[RiskLevel.CRITICAL]:
            risk = RiskLevel.CRITICAL

    return risk


def requires_confirmation(risk: RiskLevel) -> bool:
    return risk != RiskLevel.LOW


def requires_human_review(risk: RiskLevel) -> bool:
    return risk == RiskLevel.CRITICAL
