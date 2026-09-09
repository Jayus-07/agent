"""customer_service/state_machine.py — Conversation dual-dimension state machine

Two orthogonal dimensions:
  conversation_status : open | pending | resolved | snoozed
  handling_mode       : ai | human | waiting_human

All transitions are validated here before persistence.  The Manager calls
``transition()`` which returns the new state or raises
``InvalidTransitionError``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from backend.customer_service.errors import BusinessRuleError

if TYPE_CHECKING:
    from backend.customer_service.models.conversation import CSConversation


class ConvStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    SNOOZED = "snoozed"


class HandlingMode(str, Enum):
    AI = "ai"
    HUMAN = "human"
    WAITING_HUMAN = "waiting_human"


# ── valid transitions ──────────────────────────────────────────────

VALID_STATUS_TRANSITIONS: dict[ConvStatus, frozenset[ConvStatus]] = {
    ConvStatus.OPEN: frozenset({ConvStatus.PENDING, ConvStatus.RESOLVED, ConvStatus.SNOOZED}),
    ConvStatus.PENDING: frozenset({ConvStatus.OPEN, ConvStatus.RESOLVED}),
    ConvStatus.RESOLVED: frozenset({ConvStatus.OPEN}),
    ConvStatus.SNOOZED: frozenset({ConvStatus.OPEN}),
}

VALID_MODE_TRANSITIONS: dict[HandlingMode, frozenset[HandlingMode]] = {
    HandlingMode.AI: frozenset({HandlingMode.HUMAN, HandlingMode.WAITING_HUMAN}),
    HandlingMode.HUMAN: frozenset({HandlingMode.AI}),
    HandlingMode.WAITING_HUMAN: frozenset({HandlingMode.HUMAN, HandlingMode.AI}),
}


@dataclass(frozen=True)
class TransitionResult:
    """Immutable snapshot of the state after a transition."""
    conversation_status: ConvStatus
    handling_mode: HandlingMode
    changed: bool


class InvalidTransitionError(BusinessRuleError):
    def __init__(self, dimension: str, src: str, dst: str, **kw):
        super().__init__(
            f"Invalid {dimension} transition: {src} → {dst}",
            user_message="当前状态不允许此操作",
            **kw,
        )


class InvariantViolationError(BusinessRuleError):
    def __init__(self, message: str, **kw):
        super().__init__(message, user_message=message, **kw)


def _validate_status_transition(src: ConvStatus, dst: ConvStatus) -> None:
    if dst == src:
        return
    allowed = VALID_STATUS_TRANSITIONS.get(src, frozenset())
    if dst not in allowed:
        raise InvalidTransitionError("conversation_status", src.value, dst.value)


def _validate_mode_transition(src: HandlingMode, dst: HandlingMode) -> None:
    if dst == src:
        return
    allowed = VALID_MODE_TRANSITIONS.get(src, frozenset())
    if dst not in allowed:
        raise InvalidTransitionError("handling_mode", src.value, dst.value)


def _check_invariants(status: ConvStatus, mode: HandlingMode,
                      assigned_agent_id: str | None) -> None:
    """Post-transition invariants — called after every state change."""
    if status == ConvStatus.RESOLVED and mode != HandlingMode.AI:
        raise InvariantViolationError(
            "Resolved conversations must have handling_mode=ai"
        )
    if mode == HandlingMode.HUMAN and not assigned_agent_id:
        raise InvariantViolationError(
            "handling_mode=human requires an assigned agent"
        )


def transition(
    conversation: CSConversation,
    new_status: ConvStatus | None = None,
    new_mode: HandlingMode | None = None,
) -> TransitionResult:
    """Validate and compute a state transition.

    Does NOT persist — caller (ConversationManager) is responsible for
    flushing to DB after this returns successfully.

    Returns a TransitionResult with the target state.
    Raises InvalidTransitionError or InvariantViolationError on failure.
    """
    cur_status = ConvStatus(conversation.conversation_status)
    cur_mode = HandlingMode(conversation.handling_mode)

    target_status = new_status if new_status is not None else cur_status
    target_mode = new_mode if new_mode is not None else cur_mode

    _validate_status_transition(cur_status, target_status)
    _validate_mode_transition(cur_mode, target_mode)
    _check_invariants(target_status, target_mode, conversation.assigned_agent_id)

    changed = (target_status != cur_status) or (target_mode != cur_mode)
    return TransitionResult(
        conversation_status=target_status,
        handling_mode=target_mode,
        changed=changed,
    )


def apply(conversation: CSConversation, result: TransitionResult) -> None:
    """Mutate the ORM object in-place (no flush)."""
    conversation.conversation_status = result.conversation_status.value
    conversation.handling_mode = result.handling_mode.value
