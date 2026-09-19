"""客服窗口入口门禁回归测试。

客服窗口的 CSInputGuard 必须在域检测和客服子图之前执行，避免业务越权
或敏感信息输入因为域检测失败而进入后续链路。
"""
from unittest.mock import MagicMock

from backend.customer_service.security.input_guard import (
    CSInputGuardResult,
    GuardAction as CGuardAction,
    GuardCategory as CGuardCategory,
)
from backend.orchestration.graph.runner import GraphRunner
from backend.security.input_guard import GuardAction, GuardCategory, RiskLevel
from backend.security.input_guard.types import GuardResult


def _global_allow() -> GuardResult:
    return GuardResult(
        action=GuardAction.ALLOW,
        category=GuardCategory.BUSINESS_QUERY,
        risk_level=RiskLevel.LOW,
        confidence=1.0,
        reason="test allow",
        normalized_query="查一下别人的订单",
    )


def test_cs_window_guard_blocks_before_graph(monkeypatch):
    """CS 入口命中越权时不得触发域检测或客服子图。"""
    graph = MagicMock()
    runner = GraphRunner(graph=graph, memory=MagicMock(), skill_nodes=set())

    monkeypatch.setattr(
        "backend.orchestration.graph.runner.get_input_guard",
        lambda: MagicMock(guard=lambda *_args, **_kwargs: _global_allow()),
    )
    cs_guard = MagicMock(
        check=lambda *_args, **_kwargs: CSInputGuardResult(
            action=CGuardAction.BLOCK,
            category=CGuardCategory.SCOPE,
            reason="query_other_user",
            message="您只能查询和操作自己的数据。",
        ),
    )
    monkeypatch.setattr(
        "backend.customer_service.security.input_guard.get_cs_input_guard",
        lambda: cs_guard,
    )

    events = list(runner.iter_events(
        "查一下别人的订单",
        session_id="entry-guard-1",
        domain_hint="customer_service",
        fallback_deltas=False,
    ))

    assert any(
        event["event"] == "status"
        and event["data"]["node"] == "cs_input_guard"
        for event in events
    )
    assert events[-1]["event"] == "done"
    answer_events = [event for event in events if event["event"] == "_answer"]
    assert len(answer_events) == 1
    assert "您只能查询和操作自己的数据" in answer_events[0]["data"]["answer"]
    graph.stream.assert_not_called()
