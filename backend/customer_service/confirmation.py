"""customer_service/confirmation.py — 确认状态机

Phase 4 核心：业务操作必须经过用户明确确认才执行。

状态流转:
  NOT_REQUIRED → EXECUTING → SUCCESS / FAILED
  PENDING → CONFIRMED → EXECUTING → SUCCESS / FAILED
  PENDING → CANCELLED
  PENDING → EXPIRED

PENDING → EXECUTING 是非法的（必须经过 CONFIRMED）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from backend.customer_service.errors import BusinessRuleError


class ConfirmationState(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING_CONFIRMATION = "pending"
    USER_CONFIRMED = "confirmed"
    USER_CANCELLED = "cancelled"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


VALID_TRANSITIONS: dict[ConfirmationState, frozenset[ConfirmationState]] = {
    ConfirmationState.NOT_REQUIRED:       frozenset({ConfirmationState.EXECUTING}),
    ConfirmationState.PENDING_CONFIRMATION: frozenset({
        ConfirmationState.USER_CONFIRMED,
        ConfirmationState.USER_CANCELLED,
        ConfirmationState.EXPIRED,
    }),
    ConfirmationState.USER_CONFIRMED:     frozenset({ConfirmationState.EXECUTING}),
    ConfirmationState.EXECUTING:          frozenset({
        ConfirmationState.SUCCESS,
        ConfirmationState.FAILED,
    }),
}

TERMINAL_STATES = frozenset({
    ConfirmationState.SUCCESS,
    ConfirmationState.FAILED,
    ConfirmationState.USER_CANCELLED,
    ConfirmationState.EXPIRED,
})


class ConfirmationIntent(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    NONE = "none"


class ConfirmationTransitionError(BusinessRuleError):
    def __init__(self, src: ConfirmationState, dst: ConfirmationState):
        super().__init__(
            f"Invalid confirmation transition: {src.value} → {dst.value}",
            user_message="当前状态不允许此操作",
        )


@dataclass(frozen=True)
class ConfirmationTransitionResult:
    state: ConfirmationState
    changed: bool


def transition(
    current: ConfirmationState,
    target: ConfirmationState,
) -> ConfirmationTransitionResult:
    """Validate and compute a confirmation state transition.

    Raises ConfirmationTransitionError on illegal transitions.
    """
    if target == current:
        return ConfirmationTransitionResult(state=current, changed=False)

    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ConfirmationTransitionError(current, target)

    return ConfirmationTransitionResult(state=target, changed=True)


def is_terminal(state: ConfirmationState) -> bool:
    return state in TERMINAL_STATES


_CONFIRM_KEYWORDS = frozenset({
    "确认", "确定", "好的", "同意", "可以", "没问题", "是的",
    "嗯", "对", "ok", "yes", "confirm",
})

_CANCEL_KEYWORDS = frozenset({
    "取消", "算了", "不要", "不", "否", "放弃", "算了不",
    "cancel", "no",
})


def detect_confirmation_intent(text: str) -> ConfirmationIntent:
    """Detect whether the user text confirms or cancels a pending action."""
    text_lower = text.strip().lower()
    if not text_lower:
        return ConfirmationIntent.NONE

    for kw in _CANCEL_KEYWORDS:
        if kw in text_lower:
            return ConfirmationIntent.CANCEL

    for kw in _CONFIRM_KEYWORDS:
        if kw in text_lower:
            return ConfirmationIntent.CONFIRM

    return ConfirmationIntent.NONE


def compute_expires_at(
    created_at: datetime,
    ttl_seconds: int,
) -> datetime:
    return created_at + timedelta(seconds=ttl_seconds)


def is_expired(
    pending_action: dict,
    now: datetime | None = None,
) -> bool:
    """Check if a pending_action has expired based on its expires_at field."""
    expires_at_str = pending_action.get("expires_at")
    if not expires_at_str:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now >= expires_at
    except (ValueError, TypeError):
        return False
