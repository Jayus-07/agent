"""test_supervisor_v2_priority.py — Supervisor 七层固定优先级（迁移 B6）

v2 顺序即语义：1 handoff → 2 pending → 3 风险 → 4 循环预算 →
5 意图路由 → 6 低置信处理 → 7 LLM 兜底。与 v1 的差异只在多条件并存
的裁决（单条件行为等价），本文件重点锁定多条件裁决与回退开关。
"""
from __future__ import annotations

import pytest

from backend.config import customer_service as cs_config
from backend.customer_service.supervisor import (
    ExpertAction,
    ExpertType,
    make_supervisor_decision,
)


def _state(cs_route: dict | None = None, **overrides) -> dict:
    base = {
        "cs_route": cs_route if cs_route is not None else {
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


class TestSevenLayerPriority:
    """单条件行为与 v1 等价（[v2·L*] 前缀可归因）。"""

    def test_l1_handoff_intercept(self):
        d = make_supervisor_decision(_state(handoff_state="waiting_human"))
        assert d["next_action"] == ExpertAction.HANDOFF.value
        assert "[v2·L1]" in d["reason"]

    def test_l2_pending(self):
        d = make_supervisor_decision(_state(confirmation_state="pending"))
        assert d["next_action"] == ExpertAction.PENDING.value
        assert "[v2·L2]" in d["reason"]

    def test_l3_risk_finish(self):
        d = make_supervisor_decision(_state({
            "domain": "KNOWLEDGE", "route_path": "knowledge_query",
            "intent": "k_faq", "confidence": 0.9,
            "metadata": {"risk_hits": ["risk:别人的订单"]},
        }))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L3]" in d["reason"]

    def test_l4_loop_guard(self):
        d = make_supervisor_decision(_state(expert_loop_count=5))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L4]" in d["reason"]

    def test_l4_repeat_guard(self):
        history = [{"expert": "knowledge"}, {"expert": "knowledge"}]
        d = make_supervisor_decision(_state(expert_history=history))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L4]" in d["reason"]

    def test_l5_confident_route(self):
        d = make_supervisor_decision(_state())
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"
        assert "[v2·L5]" in d["reason"]

    def test_l6_lowconf_no_history_finish(self):
        route = {"domain": "QUERY", "route_path": "business_query",
                 "intent": "t_order_status", "confidence": 0.3}
        d = make_supervisor_decision(_state(cs_route=route))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L6]" in d["reason"]

    def test_l6_lowconf_knowledge_pass(self):
        route = {"domain": "KNOWLEDGE", "route_path": "knowledge_query",
                 "intent": "k_faq", "confidence": 0.3}
        d = make_supervisor_decision(_state(cs_route=route))
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"
        assert "[v2·L6]" in d["reason"]

    def test_l7_llm_decision_used(self, monkeypatch):
        import backend.customer_service.supervisor as sup

        route = {"domain": "KNOWLEDGE", "route_path": "knowledge_query",
                 "intent": "k_faq", "confidence": 0.3}
        monkeypatch.setattr(
            sup, "_llm_decision",
            lambda state: sup._make_decision(
                sup.ExpertAction.RUN_EXPERT, sup.ExpertType.QUERY, layer=3,
                reason="LLM 决策: expert=query"),
        )
        d = make_supervisor_decision(_state(
            cs_route=route, expert_history=[{"expert": "knowledge"}]))
        assert d["next_expert"] == "query"
        assert d["decision_layer"] == 3

    def test_l7_llm_unavailable_fallback_repeat_finish(self, monkeypatch):
        import backend.customer_service.supervisor as sup

        route = {"domain": "KNOWLEDGE", "route_path": "knowledge_query",
                 "intent": "k_faq", "confidence": 0.3}
        monkeypatch.setattr(sup, "_llm_decision", lambda state: None)
        d = make_supervisor_decision(_state(
            cs_route=route, expert_history=[{"expert": "knowledge"}]))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L7]" in d["reason"]

    def test_l7_llm_unavailable_fallback_route(self, monkeypatch):
        import backend.customer_service.supervisor as sup

        route = {"domain": "COMPLAINT", "route_path": "complaint_flow",
                 "intent": "c_complaint", "confidence": 0.3}
        monkeypatch.setattr(sup, "_llm_decision", lambda state: None)
        d = make_supervisor_decision(_state(
            cs_route=route, expert_history=[{"expert": "knowledge"}]))
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "complaint"
        assert "[v2·L7]" in d["reason"]


class TestMultiConditionArbitration:
    """多条件并存裁决（v2 相对 v1 的语义变化点）。"""

    def test_pending_wins_over_loop(self):
        """v1：loop guard 在前 → FINISH(loop)；v2：pending(#2) 先于循环(#4)。"""
        d = make_supervisor_decision(_state(
            confirmation_state="pending", expert_loop_count=5))
        assert d["next_action"] == ExpertAction.PENDING.value

    def test_risk_wins_over_loop(self):
        d = make_supervisor_decision(_state(
            expert_loop_count=5,
            cs_route={
                "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                "intent": "k_faq", "confidence": 0.9,
                "metadata": {"risk_hits": ["risk:直接改数据库"]},
            }))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "风险" in d["reason"]

    def test_p0_wins_over_loop(self):
        """循环耗尽不吞掉 P0 投诉直通（complaint 幂等防重入兜底）。"""
        d = make_supervisor_decision(_state(
            expert_loop_count=5,
            cs_route={
                "domain": "KNOWLEDGE", "route_path": "knowledge_query",
                "intent": "k_faq", "confidence": 0.9,
                "metadata": {"sentiment_hits": ["angry:12315"]},
            }))
        assert d["next_expert"] == ExpertType.COMPLAINT.value

    def test_p0_direct_only_once_per_turn(self):
        """B6 实机修复回归：P0 直通仅限本轮首次——complaint 已执行后不再
        直通（静态信号 + 无防重入 = supervisor⇄complaint 死循环直至
        GraphRecursionError，本用例锁死该回归）。"""
        d = make_supervisor_decision(_state(
            expert_history=[{"expert": "complaint"}],
            cs_route={
                "domain": "COMPLAINT", "route_path": "complaint_flow",
                "intent": "c_complaint", "confidence": 0.9,
                "metadata": {"sentiment_hits": ["angry:12315"]},
            }))
        assert "P0 投诉信号直通" not in d["reason"]
        assert "[v2·L5]" in d["reason"]

    def test_p0_second_visit_hits_repeat_guard(self):
        d = make_supervisor_decision(_state(
            expert_history=[{"expert": "complaint"}, {"expert": "complaint"}],
            cs_route={
                "domain": "COMPLAINT", "route_path": "complaint_flow",
                "intent": "c_complaint", "confidence": 0.9,
                "metadata": {"sentiment_hits": ["angry:12315"]},
            }))
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "[v2·L4]" in d["reason"]

    def test_v1_p0_direct_also_guarded(self, monkeypatch):
        """v1 回退路径同缺陷同修：complaint 已执行后不再直通。"""
        monkeypatch.setattr(cs_config, "CS_DECISION_V2", False)
        d = make_supervisor_decision(_state(
            expert_history=[{"expert": "complaint"}],
            cs_route={
                "domain": "COMPLAINT", "route_path": "complaint_flow",
                "intent": "c_complaint", "confidence": 0.9,
                "metadata": {"sentiment_hits": ["angry:12315"]},
            }))
        assert "P0 投诉信号直通" not in d["reason"]
        assert d["next_expert"] == "complaint"  # 走默认路由（存量语义）

    def test_route_wins_over_lowconf_branch_when_confident(self):
        """置信达标时低置信分支不参与（L5 直达路由）。"""
        d = make_supervisor_decision(_state(expert_history=[{"expert": "query"}]))
        assert "[v2·L5]" in d["reason"]


class TestRollbackSwitch:
    """CS_DECISION_V2=false 回退 v1 存量顺序。"""

    def test_switch_off_restores_v1_arbitration(self, monkeypatch):
        monkeypatch.setattr(cs_config, "CS_DECISION_V2", False)
        d = make_supervisor_decision(_state(
            confirmation_state="pending", expert_loop_count=5))
        # v1：loop guard(1b) 在 pending(2a) 之前 → FINISH(loop) 而非 PENDING
        assert d["next_action"] == ExpertAction.FINISH.value
        assert "循环" in d["reason"]
        assert "[v2·" not in d["reason"]

    def test_switch_off_single_condition_still_works(self, monkeypatch):
        monkeypatch.setattr(cs_config, "CS_DECISION_V2", False)
        d = make_supervisor_decision(_state())
        assert d["next_action"] == ExpertAction.RUN_EXPERT.value
        assert d["next_expert"] == "knowledge"
