"""tests/travel/test_booking_candidate.py — Booking/Candidate 预留（Phase 7，任务书 §12/§14）

纯数据结构与契约形状测试：无网络、无供应商、无域图行为变化。
- BookingStatus 状态机：单向流转白名单、终态封死
- 幂等键：同输入稳定、任一字段变即变、不暴露联系人原文
- CandidatePlan：字段形状与默认值
- state 预留键：candidate_plans 在 planning_reset 中被清空
- 域图行为不变：正常出单后 candidate_plans 恒为空（无节点写入）
"""
from __future__ import annotations

from typing import get_args

import pytest

from backend.providers.travel.booking import (
    BookingRequest,
    BookingStatus,
    can_transition,
    make_idempotency_key,
)
from backend.travel.graph_state import new_travel_graph_input, planning_reset
from backend.travel.models.candidate_plan import CandidatePlan


# =============================================
# 一、BookingStatus 状态机
# =============================================
class TestBookingStatus:
    def test_values(self):
        assert {s.value for s in BookingStatus} == {
            "pending", "confirmed", "cancelled", "failed", "expired"}

    def test_pending_can_move_anywhere(self):
        for target in ("confirmed", "cancelled", "failed", "expired"):
            assert can_transition(BookingStatus.PENDING,
                                  BookingStatus(target))

    def test_terminal_states_frozen(self):
        for status in BookingStatus.terminal():
            for target in BookingStatus:
                assert not can_transition(status, target), \
                    f"{status} 是终态，不允许流转到 {target}"

    def test_rejects_nonsense(self):
        assert not can_transition(BookingStatus.PENDING, BookingStatus.PENDING)


# =============================================
# 二、幂等键
# =============================================
class TestIdempotencyKey:
    def test_stable_for_same_input(self):
        a = make_idempotency_key("tx", "poi_1", "2026-10-02", 2, "user_a")
        b = make_idempotency_key("tx", "poi_1", "2026-10-02", 2, "user_a")
        assert a == b and len(a) == 16

    def test_any_field_change_changes_key(self):
        base = make_idempotency_key("tx", "poi_1", "2026-10-02", 2, "user_a")
        assert make_idempotency_key("tx", "poi_1", "2026-10-02", 2, "user_b") != base
        assert make_idempotency_key("tx", "poi_1", "2026-10-03", 2, "user_a") != base
        assert make_idempotency_key("tx", "poi_1", "2026-10-02", 3, "user_a") != base
        assert make_idempotency_key("tx", "poi_2", "2026-10-02", 2, "user_a") != base

    def test_does_not_leak_contact(self):
        key = make_idempotency_key("tx", "poi_1", "2026-10-02", 2, "13800001111")
        assert "13800001111" not in key

    def test_request_create_derives_key(self):
        r1 = BookingRequest.create("tx", "poi_1", "2026-10-02", 2, "u")
        r2 = BookingRequest.create("tx", "poi_1", "2026-10-02", 2, "u")
        assert r1.idempotency_key == r2.idempotency_key


# =============================================
# 三、CandidatePlan 形状
# =============================================
class TestCandidatePlan:
    def test_minimal_shape(self):
        plan = CandidatePlan(
            candidate_id="cand_a", objective="budget",
            itinerary={"days": []}, cost_cny=1200.0, total_minutes=480,
            violations=["error:BUDGET_OVER"])
        assert plan.quality is None  # 未经 validator 时质量分为空
        assert plan.created_at

    def test_quality_bounds(self):
        with pytest.raises(Exception):
            CandidatePlan(candidate_id="x", objective="pace",
                          itinerary={}, quality=1.5)


# =============================================
# 四、state 预留键与域图行为不变
# =============================================
class TestStateReservedKey:
    def test_reset_clears_candidate_plans(self):
        assert planning_reset()["candidate_plans"] == []

    def test_graph_input_has_no_candidate_default(self):
        # 「只放本轮输入」纪律：new_travel_graph_input 不预置执行态
        state = new_travel_graph_input("福州1天行程")
        assert "candidate_plans" not in state or state.get("candidate_plans") is None

    def test_typed_dict_declares_key(self):
        from backend.travel.graph_state import TravelGraphState
        assert "candidate_plans" in TravelGraphState.__annotations__

    def test_real_run_keeps_it_empty(self):
        """全规则图真实跑一轮：无节点写入，candidate_plans 恒空（单方案不变）。"""
        import backend.config.travel as T
        import backend.travel.graph_builder as gb

        T.TRAVEL_CHECKPOINTER_ENABLED = False
        gb._travel_graph = None
        try:
            graph = gb.get_travel_graph()
            final = graph.invoke(
                new_travel_graph_input("福州1天行程，1个人"),
                config={"configurable": {"thread_id": "t-p7-reserved"}},
            )
            assert final.get("candidate_plans") in (None, []), \
                "无节点写入 candidate_plans：缺席（LangGraph 不落未写键）或空 list"
            assert final["itinerary"]["status"] in ("ready", "degraded",
                                                    "needs_user_decision")
        finally:
            gb._travel_graph = None
