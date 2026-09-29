"""customer_service/handoff/ — 转人工域（迁移 B11 结构整理）

成员：handoff.py（状态机/触发词）/ handoff_store.py（存储）/ dispatch/（派单 P4-P8 整体迁入）。
包名承接旧模块 backend.customer_service.handoff 的对外契约（HandoffState/transition/...）。
"""
from backend.customer_service.handoff.handoff import *  # noqa: F401,F403
from backend.customer_service.handoff.handoff import (  # noqa: F401
    HandoffState,
    HandoffTransitionError,
    HandoffTransitionResult,
    INTERCEPT_STATES,
    TERMINAL_STATES,
    TriggerType,
    is_ai_active,
    is_terminal,
    should_intercept,
    transition,
)
