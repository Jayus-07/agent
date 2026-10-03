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
    # 结果未知（2026-09-22 Tool 治理）：写操作 timeout 后实际可能已执行成功
    # 但响应丢失 —— 进入 VERIFYING 待对账（查操作状态），禁止盲目重新提交
    VERIFYING = "verifying"
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
        ConfirmationState.VERIFYING,
    }),
    ConfirmationState.VERIFYING:          frozenset({
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


# 确认/取消词表收敛至 vocab.py 单一事实源（迁移 B8，P1 修正版为唯一
# 权威）；经 accessor 读取以支持热加载（override 文件 + 变更台账）。
from backend.customer_service.vocab import (  # noqa: E402
    QUESTION_MARKERS as _QUESTION_MARKERS,
    get_cancel_keywords as _get_cancel_keywords,
    get_confirm_keywords as _get_confirm_keywords,
)


def _is_question_form(text_lower: str) -> bool:
    """疑问/假设句式守卫（词表 QUESTION_MARKERS + 句尾「么」锚定）。

    2026-10-03（C10 门禁基线）：裸「么」子串守卫过宽（"确定，就这么办"
    被误判疑问），收窄为词表枚举后改用句尾锚定补「需要确认么」类疑问。
    """
    from backend.customer_service.vocab import QUESTION_MARKERS
    if any(marker in text_lower for marker in QUESTION_MARKERS):
        return True
    return text_lower.rstrip("。！!？?~～，,  、；;…").endswith("么")


def detect_confirmation_intent(text: str) -> ConfirmationIntent:
    """Detect whether the user text confirms or cancels a pending action."""
    text_lower = text.strip().lower()
    if not text_lower:
        return ConfirmationIntent.NONE

    if _is_question_form(text_lower):
        return ConfirmationIntent.NONE

    for kw in _get_cancel_keywords():
        if kw in text_lower:
            return ConfirmationIntent.CANCEL

    for kw in _get_confirm_keywords():
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
