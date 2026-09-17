"""customer_service/handoff.py — 人工转接状态机

5 状态流转:
  AI_ACTIVE → HANDOFF_REQUESTED → WAITING_HUMAN → HUMAN_ACTIVE → CLOSED
  HANDOFF_REQUESTED → AI_ACTIVE (回退)

纯函数模块 — 不持有状态，只校验转换并返回结果。
持久化由 HandoffStore 负责。

设计参考: docs/customer-service/design.md §10
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from backend.customer_service.errors import BusinessRuleError


class HandoffState(str, Enum):
    AI_ACTIVE = "ai_active"
    HANDOFF_REQUESTED = "handoff_requested"
    WAITING_HUMAN = "waiting_human"
    HUMAN_ACTIVE = "human_active"
    CLOSED = "closed"


VALID_TRANSITIONS: dict[HandoffState, frozenset[HandoffState]] = {
    HandoffState.AI_ACTIVE: frozenset({
        HandoffState.HANDOFF_REQUESTED,
        HandoffState.CLOSED,
    }),
    HandoffState.HANDOFF_REQUESTED: frozenset({
        HandoffState.WAITING_HUMAN,
        HandoffState.AI_ACTIVE,
        HandoffState.CLOSED,
    }),
    HandoffState.WAITING_HUMAN: frozenset({
        HandoffState.HUMAN_ACTIVE,
        HandoffState.CLOSED,
    }),
    HandoffState.HUMAN_ACTIVE: frozenset({
        HandoffState.CLOSED,
    }),
    HandoffState.CLOSED: frozenset(),
}

TERMINAL_STATES = frozenset({HandoffState.CLOSED})

INTERCEPT_STATES = frozenset({
    HandoffState.HANDOFF_REQUESTED,
    HandoffState.WAITING_HUMAN,
    HandoffState.HUMAN_ACTIVE,
})


class HandoffTransitionError(BusinessRuleError):
    def __init__(self, src: HandoffState, dst: HandoffState):
        super().__init__(
            f"Invalid handoff transition: {src.value} → {dst.value}",
            user_message="当前状态不允许此操作",
        )


@dataclass(frozen=True)
class HandoffTransitionResult:
    state: HandoffState
    changed: bool


def transition(
    current: HandoffState,
    target: HandoffState,
) -> HandoffTransitionResult:
    """Validate and compute a handoff state transition.

    Raises HandoffTransitionError on illegal transitions.
    """
    if target == current:
        return HandoffTransitionResult(state=current, changed=False)

    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise HandoffTransitionError(current, target)

    return HandoffTransitionResult(state=target, changed=True)


def is_terminal(state: HandoffState) -> bool:
    return state in TERMINAL_STATES


def is_ai_active(state: HandoffState) -> bool:
    return state == HandoffState.AI_ACTIVE


def should_intercept(state: HandoffState) -> bool:
    """Return True when the handoff state requires intercepting business requests."""
    return state in INTERCEPT_STATES


# ── Trigger detection ──────────────────────────────────────────────

_HANDOFF_KEYWORDS = frozenset({
    # 2026-09-17 收窄：移除裸词「人工/真人/转接」——"人工成本分析"这类
    # 业务问题会被误伤（直通层放大了误伤面）。常见说法已由 _HANDOFF_PATTERNS
    # 的「转/找 + 对象」结构与「人工服务」模式覆盖，裸词命中属于过度匹配。
    "找客服", "转人工",
    "不要机器人", "找经理", "找主管",
})

_HANDOFF_PATTERNS = [
    re.compile(r"(转|找).*(人工|真人|客服|经理|主管)"),
    re.compile(r"人工服务"),
    re.compile(r"不要.*机器人"),
    re.compile(r"你是.*机器人.*吗"),
]


class TriggerType(str, Enum):
    EXPLICIT_REQUEST = "explicit_request"
    LOW_CONFIDENCE = "low_confidence"
    CONSECUTIVE_FAILURES = "consecutive_failures"
    COMPLAINT_ESCALATION = "complaint_escalation"
    HIGH_RISK_ACTION = "high_risk_action"
    NONE = "none"


# 转人工原因 → 质量分析口径（三层拆分，转人工率按此分桶）
#   healthy_user: 用户主动要求 —— 产品正常行为
#   healthy_escalation: 业务升级/高危确认 —— 产品设计使然
#   capability_gap: AI 能力不足被迫转人工 —— 目标趋近 0
HANDOFF_REASON_BUCKETS = {
    TriggerType.EXPLICIT_REQUEST.value: "healthy_user",
    TriggerType.LOW_CONFIDENCE.value: "capability_gap",
    TriggerType.CONSECUTIVE_FAILURES.value: "capability_gap",
    TriggerType.COMPLAINT_ESCALATION.value: "healthy_escalation",
    TriggerType.HIGH_RISK_ACTION.value: "healthy_escalation",
    TriggerType.NONE.value: "capability_gap",  # 无明确 trigger 的转人工按能力缺口处理
}


def handoff_reason_bucket(trigger_type: str) -> str:
    """trigger_type 值 → 分析桶；loop_limit 等来源在聚合层单独归类。"""
    return HANDOFF_REASON_BUCKETS.get(trigger_type, "capability_gap")


@dataclass(frozen=True)
class HandoffTrigger:
    trigger_type: TriggerType
    reason: str
    confidence: float = 1.0


def detect_handoff_trigger(text: str) -> HandoffTrigger | None:
    """Detect explicit handoff request from user text."""
    text_stripped = text.strip()
    if not text_stripped:
        return None

    for pattern in _HANDOFF_PATTERNS:
        if pattern.search(text_stripped):
            return HandoffTrigger(
                trigger_type=TriggerType.EXPLICIT_REQUEST,
                reason=f"用户显式请求人工服务: {text_stripped[:50]}",
            )

    for kw in _HANDOFF_KEYWORDS:
        if kw in text_stripped:
            return HandoffTrigger(
                trigger_type=TriggerType.EXPLICIT_REQUEST,
                reason=f"用户显式请求人工服务: 关键词 '{kw}'",
            )

    return None


def evaluate_auto_triggers(cs_context: dict) -> HandoffTrigger | None:
    """Evaluate automatic handoff triggers from conversation context.

    Checks:
      - low_confidence: router confidence below threshold for consecutive turns
      - consecutive_failures: consecutive failed answer attempts
    """
    from backend.config.customer_service import (
        CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT,
        CS_HANDOFF_LOW_CONF_THRESHOLD,
    )

    consecutive_low = cs_context.get("consecutive_low_confidence", 0)
    if consecutive_low >= 2:
        confidence = cs_context.get("last_confidence", 0.0)
        if confidence < CS_HANDOFF_LOW_CONF_THRESHOLD:
            return HandoffTrigger(
                trigger_type=TriggerType.LOW_CONFIDENCE,
                reason=f"连续 {consecutive_low} 次低置信度 (最近: {confidence:.2f})",
                confidence=confidence,
            )

    consecutive_fails = cs_context.get("consecutive_failures", 0)
    if consecutive_fails >= CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT:
        return HandoffTrigger(
            trigger_type=TriggerType.CONSECUTIVE_FAILURES,
            reason=f"连续 {consecutive_fails} 次回答失败",
        )

    return None
