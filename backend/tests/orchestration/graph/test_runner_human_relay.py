"""人工接管期与全局 InputGuard 的回归测试。"""

from backend.orchestration.graph.runner import (
    _should_bypass_guard_for_human_relay,
)
from backend.security.input_guard import (
    GuardAction,
    GuardCategory,
    GuardResult,
    RiskLevel,
)


def _guard_result(category: GuardCategory, risk: RiskLevel = RiskLevel.LOW):
    return GuardResult(
        action=GuardAction.CLARIFY,
        category=category,
        risk_level=risk,
        confidence=0.9,
        reason="test clarify",
        normalized_query="123",
    )


def test_low_risk_clarify_is_bypassed_during_human_relay(monkeypatch):
    """人工处理中发送短消息时，不能再次被 Agent 门禁截断。"""
    monkeypatch.setattr(
        "backend.orchestration.graph.cs_prefilter._active_relay_state",
        lambda user_id, session_id: "human_active",
    )

    assert _should_bypass_guard_for_human_relay(
        _guard_result(GuardCategory.GARBAGE),
        domain_hint="customer_service",
        user_id="user-1",
        session_id="session-1",
    ) is True


def test_human_relay_does_not_bypass_security_clarify(monkeypatch):
    """人工处理中也不能放过中风险安全澄清。"""
    monkeypatch.setattr(
        "backend.orchestration.graph.cs_prefilter._active_relay_state",
        lambda user_id, session_id: "human_active",
    )

    assert _should_bypass_guard_for_human_relay(
        _guard_result(GuardCategory.PROMPT_INJECTION, RiskLevel.MEDIUM),
        domain_hint="customer_service",
        user_id="user-1",
        session_id="session-1",
    ) is False


def test_inactive_session_does_not_bypass_guard(monkeypatch):
    """没有人工接管状态时，普通客服输入仍遵守原门禁。"""
    monkeypatch.setattr(
        "backend.orchestration.graph.cs_prefilter._active_relay_state",
        lambda user_id, session_id: None,
    )

    assert _should_bypass_guard_for_human_relay(
        _guard_result(GuardCategory.GARBAGE),
        domain_hint="customer_service",
        user_id="user-1",
        session_id="session-1",
    ) is False
