"""tests/customer_service/test_cs_supervisor.py — CS Supervisor 三层决策测试"""
from __future__ import annotations

from unittest.mock import patch

from backend.customer_service.graph_state import (
    CS_ACTION_EXPERT,
    CS_COMPLAINT_EXPERT,
    CS_HANDOFF_EXPERT,
    CS_KNOWLEDGE_EXPERT,
    CS_QUERY_EXPERT,
    CS_REPORTER,
)
from backend.customer_service.supervisor import (
    ExpertAction,
    ExpertType,
    _is_expert_repeating,
    _resolve_expert,
    cs_supervisor_node,
    make_supervisor_decision,
)


def _state(**overrides) -> dict:
    base = {
        "cs_route": {
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "intent": "k_faq",
            "confidence": 0.90,
        },
        "handoff_state": "ai_active",
        "confirmation_state": "not_required",
        "expert_loop_count": 0,
        "expert_history": [],
        "user_message": "test question",
    }
    base.update(overrides)
    return base


class TestResolveExpert:
    def test_route_path_takes_priority(self):
        assert _resolve_expert({"route_path": "business_query", "domain": "KNOWLEDGE"}) == "query"

    def test_domain_fallback(self):
        assert _resolve_expert({"domain": "COMPLAINT"}) == "complaint"

    def test_unknown_defaults_to_knowledge(self):
        assert _resolve_expert({}) == "knowledge"

    def test_all_route_paths(self):
        mapping = {
            "knowledge_query": "knowledge",
            "business_query": "query",
            "business_action": "action",
            "complaint_flow": "complaint",
            "human_handoff": "handoff",
        }
        for rp, expected in mapping.items():
            assert _resolve_expert({"route_path": rp}) == expected


class TestIsExpertRepeating:
    def test_empty_history(self):
        assert _is_expert_repeating([]) is False

    def test_single_entry(self):
        assert _is_expert_repeating([{"expert": "knowledge"}]) is False

    def test_same_expert_twice(self):
        assert _is_expert_repeating([
            {"expert": "knowledge"},
            {"expert": "knowledge"},
        ]) is True

    def test_different_experts(self):
        assert _is_expert_repeating([
            {"expert": "knowledge"},
            {"expert": "query"},
        ]) is False


class TestLayer1Rules:
    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_intercept(self):
        s = _state(handoff_state="handoff_requested")
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.HANDOFF.value
        assert d["next_expert"] == ExpertType.HANDOFF.value
        assert d["decision_layer"] == 1
        assert d["requires_handoff"] is True
        assert d["is_finished"] is True

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_intercept_waiting_human(self):
        s = _state(handoff_state="waiting_human")
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.HANDOFF.value
        assert d["is_finished"] is True

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_intercept_human_active(self):
        s = _state(handoff_state="human_active")
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.HANDOFF.value
        assert d["is_finished"] is True

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_ai_active_not_intercepted(self):
        s = _state(handoff_state="ai_active")
        d = make_supervisor_decision(s)
        assert d["next_action"] != ExpertAction.HANDOFF.value

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_empty_not_intercepted(self):
        s = _state(handoff_state="")
        d = make_supervisor_decision(s)
        assert d["next_action"] != ExpertAction.HANDOFF.value

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 3)
    def test_loop_guard(self):
        s = _state(expert_loop_count=3)
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["decision_layer"] == 1
        assert d["is_finished"] is True

    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_low_confidence_knowledge_passthrough(self):
        """P2.1（audit #156）：低置信但意图为知识类（低风险无权限）→ 放行检索。

        旧行为直接 finish —— 知识库明明可答却拿泛化兜底（标尺错位误拒）。
        """
        s = _state(cs_route={
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "confidence": 0.30,
        })
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"
        assert d["decision_layer"] == 1
        assert "放行知识检索" in d["reason"]

    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_confidence_fallback_no_history_action_blocked(self):
        """低置信 + 非知识类（动作/高权限）→ 维持降级兜底。"""
        s = _state(cs_route={
            "domain": "AFTER_SALES",
            "route_path": "business_action",
            "confidence": 0.30,
        })
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["decision_layer"] == 1
        assert d["is_finished"] is True

    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_confidence_fallback_no_history_auth_required_blocked(self):
        """低置信 + 知识类但 requires_auth=True → 维持降级兜底。"""
        s = _state(cs_route={
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "confidence": 0.30,
            "requires_auth": True,
        })
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["is_finished"] is True


class TestLayer2StateCombinations:
    def test_confirmation_pending(self):
        s = _state(confirmation_state="pending")
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.PENDING.value
        assert d["decision_layer"] == 2
        assert d["requires_confirmation"] is True
        assert d["is_finished"] is True

    def test_confirmation_pending_confirmation(self):
        s = _state(confirmation_state="pending_confirmation")
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.PENDING.value
        assert d["decision_layer"] == 2

    def test_expert_repeating_detection(self):
        s = _state(expert_history=[
            {"expert": "knowledge", "status": "success"},
            {"expert": "knowledge", "status": "success"},
        ])
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["decision_layer"] == 2


class TestDefaultRouting:
    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_high_confidence_routes_to_expert(self):
        s = _state(cs_route={
            "domain": "KNOWLEDGE",
            "route_path": "knowledge_query",
            "confidence": 0.90,
        })
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"

    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_route_path_business_query(self):
        s = _state(cs_route={
            "domain": "TRANSACTION",
            "route_path": "business_query",
            "confidence": 0.85,
        })
        d = make_supervisor_decision(s)
        assert d["next_expert"] == "query"

    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_route_path_complaint_flow(self):
        s = _state(cs_route={
            "domain": "COMPLAINT",
            "route_path": "complaint_flow",
            "confidence": 0.80,
        })
        d = make_supervisor_decision(s)
        assert d["next_expert"] == "complaint"


class TestLayer3LLM:
    @patch("backend.config.customer_service.CS_SUPERVISOR_LLM_ENABLED", False)
    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_llm_disabled_same_expert_just_ran_finishes(self):
        """P2.1 跟进：低置信 + LLM 不可用 + 同 expert 刚执行 → 直接收尾。

        旧行为盲目重派同一 expert（重跑浪费，靠 2b 重复检测第 3 次才拦）。
        """
        s = _state(
            cs_route={
                "domain": "KNOWLEDGE",
                "route_path": "knowledge_query",
                "confidence": 0.40,
            },
            expert_history=[{"expert": "knowledge", "status": "success"}],
        )
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.FINISH.value
        assert d["decision_layer"] == 3
        assert d["is_finished"] is True
        assert "防止重复" in d["reason"]

    @patch("backend.config.customer_service.CS_SUPERVISOR_LLM_ENABLED", False)
    @patch("backend.config.customer_service.CS_CONFIDENCE_CAUTIOUS", 0.60)
    def test_llm_disabled_different_expert_falls_through(self):
        """低置信 + LLM 不可用 + 目标 expert 与历史不同 → 降级放行（layer=3）。"""
        s = _state(
            cs_route={
                "domain": "TRANSACTION",
                "route_path": "business_query",
                "confidence": 0.40,
            },
            expert_history=[{"expert": "knowledge", "status": "success"}],
        )
        d = make_supervisor_decision(s)
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "query"
        assert d["decision_layer"] == 3
        assert "LLM 决策不可用" in d["reason"]


class TestCSupervisorNodeCommandRouting:
    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_intercept_routes_to_reporter(self):
        state = _state(handoff_state="handoff_requested")
        cmd = cs_supervisor_node(state)
        assert cmd.goto == CS_REPORTER

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_handoff_intercept_human_active_routes_to_reporter(self):
        state = _state(handoff_state="human_active")
        cmd = cs_supervisor_node(state)
        assert cmd.goto == CS_REPORTER

    @patch("backend.config.customer_service.CS_EXPERT_MAX_LOOPS", 5)
    def test_finish_routes_to_reporter(self):
        state = _state(handoff_state="ai_active", cs_route={
            "domain": "KNOWLEDGE", "route_path": "knowledge_query", "confidence": 0.9,
        })
        cmd = cs_supervisor_node(state)
        assert cmd.goto == CS_KNOWLEDGE_EXPERT
