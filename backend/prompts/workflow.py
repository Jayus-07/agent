"""Prompt version status workflow — 6-state CI/CD pipeline.

Status flow:
  draft → testing → evaluation → passed → published → archived

Forward (+1) transitions are always allowed.
Backward (-1) transitions are allowed only from testing/evaluation/passed.
Published and archived are terminal-like: published can only advance to archived;
archived is a true terminal state.
"""
from __future__ import annotations

PROMPT_STATUSES = (
    "draft",
    "testing",
    "evaluation",
    "passed",
    "published",
    "archived",
)

STATUS_INDEX: dict[str, int] = {s: i for i, s in enumerate(PROMPT_STATUSES)}

_BACKWARD_ALLOWED_FROM = {"testing", "evaluation", "passed"}


class StatusTransitionError(ValueError):
    """Raised when a status transition is not allowed."""


def validate_transition(current: str, target: str) -> None:
    """Validate that transitioning from *current* to *target* is legal.

    Raises StatusTransitionError if the transition is forbidden.
    """
    if current not in STATUS_INDEX:
        raise StatusTransitionError(f"Unknown status: {current}")
    if target not in STATUS_INDEX:
        raise StatusTransitionError(f"Unknown status: {target}")
    if current == target:
        raise StatusTransitionError(f"Cannot transition to same status: {current}")

    cur_idx = STATUS_INDEX[current]
    tgt_idx = STATUS_INDEX[target]
    diff = tgt_idx - cur_idx

    if diff == 1:
        return

    if diff == -1 and current in _BACKWARD_ALLOWED_FROM:
        return

    raise StatusTransitionError(
        f"Transition {current} → {target} is not allowed"
    )
