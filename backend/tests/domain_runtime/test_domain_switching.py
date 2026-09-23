# -*- coding: utf-8 -*-
"""test_domain_switching.py — 切域行为契约（STOP B B4/B5）

锁定：CS 活跃（含 pending 确认）不粘滞——他域强信号请求正常切域且不把
CS 上下文带进新域的路由更新；travel 活跃任务的补槽/重规划继续回 travel
（continuity binding，STOP C 的另一半）。

只 mock 外部边界（CS 检测器缓存），router_node 裸调（与
test_router_prefilter_order.py 同一 harness）。
"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.router import domain_detector as dd
from backend.orchestration.graph import router_node as rn


def _fake_detection(is_cs: bool = False):
    d = MagicMock()
    d.is_cs = is_cs
    d.confidence = 0.9
    d.domain = "AFTER_SALES"
    d.rule_hits = []
    d.rule_score = 0.0
    d.vector_score = 0.0
    d.reason = ""
    return d


@pytest.fixture
def fake_detector(monkeypatch):
    det = MagicMock()
    det.rule_hit_count = 0
    det._rule_channel.return_value = ([], 0.0)
    det.detect.return_value = _fake_detection(is_cs=False)
    monkeypatch.setattr(dd, "get_domain_detector", lambda: det)
    monkeypatch.setattr(dd, "_DETECT_CACHE", {})
    return det


@pytest.fixture(autouse=True)
def _no_cs_router_cache():
    from backend.customer_service.router import cs_router as cs_router_mod
    original_get = cs_router_mod._cs_cache.get_json
    original_set = cs_router_mod._cs_cache.set_json
    cs_router_mod._cs_cache.get_json = lambda key: None
    cs_router_mod._cs_cache.set_json = lambda key, value: None
    yield
    cs_router_mod._cs_cache.get_json = original_get
    cs_router_mod._cs_cache.set_json = original_set


@pytest.fixture
def domain_flags(monkeypatch):
    import backend.config.customer_service as cc
    import backend.config.travel as tc
    monkeypatch.setattr(cc, "CS_ENABLED", True)
    monkeypatch.setattr(tc, "TRAVEL_ENABLED", True)


def _cs_active_state(question: str, session_id: str = "s-switch") -> dict:
    """上一轮在客服域、且有未完成确认的状态快照（B4 场景的最坏情况）。"""
    return {
        "question": question,
        "session_id": session_id,
        "tenant_id": "t1",
        "user_id": "u1",
        "routing_context": {
            "active_domain": "customer_service",
            "last_intent": "AFTER_SALES",
            "last_action": "cs_graph_node",
            "brief_summary": {},
            "pending_question": "",
        },
        "cs_context": {
            "conversation_id": session_id,
            "confirmation_state": "pending_confirmation",
            "pending_action": {
                "action_type": "apply_refund",
                "target_id": "MO-1001",
                "status": "pending",
            },
            "cs_route": {"metadata": {"order_id": "MO-1001"}},
        },
    }


class TestSwitchAwayFromCs:
    def test_cs_pending_does_not_trap_travel_request(
        self, fake_detector, domain_flags,
    ):
        """CS 有 pending 确认 + 用户明确要规划旅游 → 必须切 travel（不粘滞）。"""
        out = rn.router_node(_cs_active_state("下周去大阪旅游，帮我做一份攻略"))
        assert out.get("route_mode") == "travel"
        assert "travel_context" in out

    def test_switch_adds_no_cs_state_to_travel_turn(
        self, fake_detector, domain_flags,
    ):
        """router_node 按惯例全量回显 state（{**state, **update}），但它在
        travel 轮**新增**的路由更新里不得出现任何 CS 键（不消费/不改写
        CS pending——pending 归 CS 业务状态机，travel 轮原样透传但不激活）。"""
        state = _cs_active_state("下周去大阪旅游，帮我做一份攻略")
        out = rn.router_node(state)
        added = {k: v for k, v in out.items() if k not in state}
        for key in ("cs_context", "cs_pending_action", "cs_action_result",
                    "cs_audit_entries", "pending_action"):
            assert key not in added, key

    def test_cs_pending_state_is_not_consumed_by_other_domain(
        self, fake_detector, domain_flags,
    ):
        """router 不得消费/改写 CS pending——切域轮更新不含 pending_action。"""
        state = _cs_active_state("下周去大阪旅游，帮我做一份攻略")
        out = rn.router_node(state)
        assert "pending_action" not in out


class TestSwitchContinuityOnTravel:
    def test_active_travel_new_run_sticks_to_travel(
        self, fake_detector, domain_flags,
    ):
        """travel 活跃任务（pending 补槽期）+ 重新规划诉求 → 继续 travel
        （TravelPendingResolver new_run 通道，continuity binding）。"""
        state = {
            "question": "重新规划一个北海道五天的行程",
            "session_id": "s-cont",
            "tenant_id": "t1",
            "user_id": "u1",
            "routing_context": {
                "active_domain": "travel",
                "last_intent": "travel",
                "last_action": "travel_graph_node",
                "brief_summary": {
                    "travel_run_id": "run-1",
                    "travel_stage": "slot",
                    "travel_pending": {
                        "requested_slots": ["destination"],
                        "question_id": "q1",
                    },
                },
                "pending_question": "想去哪里？",
            },
        }
        out = rn.router_node(state)
        assert out.get("route_mode") == "travel"
        route = (out.get("travel_context") or {}).get("travel_route") or {}
        assert route.get("source") == "pending_resume"
        assert route.get("resume_mode") == "new_run"
