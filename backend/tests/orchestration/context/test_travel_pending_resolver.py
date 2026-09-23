"""tests/orchestration/context/test_travel_pending_resolver.py — STOP F2

TravelPendingResolver 单测：纯槽位值回答的判定与放行边界。

场景映射（任务书 §22）：
  T4  conversation isolation：conv-A 有 pending，conv-B 同输入不命中
  T5  user isolation：同 conv 不同 user 不串（store 三元组主键）
  G4  短答案绕过普通 domain classification（budget/party/lodging 等命中）
  §10 客服强信号放行（「订单里的行程单」属客服，不与 CS 优先竞争）
  §8  NEW_RUN 显式信号判定
"""
from __future__ import annotations

from backend.orchestration.context.travel_pending_resolver import (
    resolve_travel_pending,
)
from backend.travel.slot_filler import is_new_run_query


def _routing(active_domain="travel", requested=None, run_id="trv_x_001",
             conversation_id="conv-1"):
    """构造 Context Assembler 产出的 routing_context 形态。"""
    return {
        "conversation_id": conversation_id,
        "active_domain": active_domain,
        "last_intent": "",
        "last_action": "",
        "pending_question": "还需要确认：玩几天？",
        "brief_summary": {
            "travel_run_id": run_id,
            "travel_stage": "slot",
            "travel_pending": {
                "question_id": "tq_test",
                "run_id": run_id,
                "requested_slots": requested or ["days"],
                "reason": "missing_required",
            },
        },
    }


class TestG4ShortAnswerHit:
    """G4：短答案绕过普通 domain classification，恢复 travel pending。"""

    def test_pure_number_days_hit(self):
        update = resolve_travel_pending("3天", _routing(requested=["days"]))
        assert update is not None
        assert update["route_mode"] == "travel"
        route = update["travel_context"]["travel_route"]
        assert route["source"] == "pending_resume"
        assert route["resume_mode"] == "continue"
        assert "days" in route["filled_slots"]

    def test_chinese_number_hit(self):
        update = resolve_travel_pending("三天", _routing(requested=["days"]))
        assert update is not None
        assert "days" in update["travel_context"]["travel_route"]["filled_slots"]

    def test_budget_hit(self):
        update = resolve_travel_pending("预算8万", _routing(requested=["budget_cny"]))
        assert update is not None
        assert "budget_cny" in update["travel_context"]["travel_route"]["filled_slots"]

    def test_party_size_explicit_hit(self):
        update = resolve_travel_pending("4个人", _routing(requested=["party_size"]))
        assert update is not None
        assert "party_size" in update["travel_context"]["travel_route"]["filled_slots"]

    def test_lodging_hit(self):
        update = resolve_travel_pending("住难波", _routing(requested=["lodging"]))
        assert update is not None
        assert "lodging" in update["travel_context"]["travel_route"]["filled_slots"]

    def test_multi_slot_answer_hits_all(self):
        """T2：多槽一次补齐（「8万，住难波」）。"""
        update = resolve_travel_pending(
            "8万，住难波", _routing(requested=["budget_cny", "lodging"]))
        assert update is not None
        filled = update["travel_context"]["travel_route"]["filled_slots"]
        assert "budget_cny" in filled and "lodging" in filled

    def test_unrelated_short_query_misses(self):
        """短但与 pending 无关（「谢谢」）→ 不拦，交回正常路由。"""
        assert resolve_travel_pending("谢谢", _routing(requested=["days"])) is None


class TestGuardConditions:
    def test_no_active_domain_never_intercepts(self):
        assert resolve_travel_pending("3天", _routing(active_domain="")) is None

    def test_no_pending_never_intercepts(self):
        rt = _routing()
        rt["brief_summary"]["travel_pending"] = None
        assert resolve_travel_pending("3天", rt) is None

    def test_cs_strong_signal_passes_through(self):
        """客服强信号放行：「订单里的行程单有问题」不被 travel pending 拦截。"""
        assert resolve_travel_pending(
            "我的订单怎么还没发货，查一下订单", _routing(requested=["days"])) is None

    def test_disabled_via_config(self, monkeypatch):
        import backend.config.travel as T

        monkeypatch.setattr(T, "TRAVEL_PENDING_RESUME_ENABLED", False)
        assert resolve_travel_pending("3天", _routing(requested=["days"])) is None

    def test_long_query_not_intercepted(self):
        assert resolve_travel_pending("帮我查一下上周的销售额环比报表数据",
                                      _routing(requested=["days"])) is None


class TestNewRun:
    def test_new_run_signal_hit(self):
        update = resolve_travel_pending(
            "不去福州了，重新规划杭州5天", _routing(requested=["days"]))
        assert update is not None
        route = update["travel_context"]["travel_route"]
        assert route["new_run"] is True
        assert route["resume_mode"] == "new_run"

    def test_is_new_run_query_pure(self):
        assert is_new_run_query("重新规划一下")
        assert is_new_run_query("换个方案")
        assert not is_new_run_query("3天")
        assert not is_new_run_query("预算改成6万")


class TestT4T5Isolation:
    def test_t4_conversation_isolation(self):
        """conv-A 有 pending，conv-B 的同输入不触发 conv-A 的 resume。"""
        # conv-B 无 pending（requested 空 → brief_summary.travel_pending=None）
        rt_b = _routing(conversation_id="conv-B")
        rt_b["brief_summary"]["travel_pending"] = None
        assert resolve_travel_pending("3天", rt_b) is None
        # conv-A 仍命中（互不影响）
        assert resolve_travel_pending("3天", _routing(conversation_id="conv-A")) is not None

    def test_t5_user_isolation(self):
        """isolation 由 store 三元组主键保证：不同 user 的 routing_context
        各自组装（active_domain/travel_pending 互不可见）。"""
        rt_user_b = _routing(active_domain="", conversation_id="conv-1")
        # user B 无活跃任务 → 不拦（即便 query 与 user A 的回答一模一样）
        assert resolve_travel_pending("3天", rt_user_b) is None
