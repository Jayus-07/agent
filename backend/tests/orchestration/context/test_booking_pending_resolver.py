"""tests/orchestration/context/test_booking_pending_resolver.py — Phase 5 / D2

交易挂起续填 Resolver（BookingPendingResolver）单测：两跳断片回归 + 放行边界。

场景映射（Phase 5 审计报告 §六 Commit A ⑤）：
  两跳回归    反问「哪天入住？」→ 用户答「10月3日」→ 续填回子图
  G1 守护     _ROUTE_MODE_DOMAIN 补登 travel_booking / travel_commerce
  放行边界    CS 强信号 / 无挂起 / 无活跃交易域 / 域开关关 / 长句 / 无关短句
  同源纪律    命中判定复用 commerce/extract.merge_slot_values（无第二套抽取）
"""
from __future__ import annotations

import pytest

from backend.orchestration.context.booking_pending_resolver import (
    resolve_booking_pending,
)


@pytest.fixture(autouse=True)
def _force_enabled(monkeypatch):
    """把两类开关钉成「开」，使本套用例不受本机 .env 影响（确定性）。

    覆盖 resolver 的两个门：全局续填开关 + 对应交易域总开关。
    """
    import backend.config.travel as T
    import backend.config.travel_booking as B
    import backend.config.travel_commerce as C

    monkeypatch.setattr(T, "BOOKING_PENDING_RESUME_ENABLED", True)
    monkeypatch.setattr(B, "is_booking_enabled", lambda: True)
    monkeypatch.setattr(C, "is_commerce_enabled", lambda: True)


def _routing(active_domain="travel_booking", route_mode="travel_booking",
             kind="hotel", missing=None, collected=None,
             conversation_id="conv-1"):
    """构造 Context Assembler 产出的 routing_context 形态。"""
    return {
        "conversation_id": conversation_id,
        "active_domain": active_domain,
        "last_intent": route_mode,
        "last_action": "clarify",
        "pending_question": "预订还需要：入住日期、退房日期。",
        "brief_summary": {
            "booking_intent": {
                "question_id": "bq_test",
                "route_mode": route_mode,
                "kind": kind,
                "missing_slots": missing if missing is not None
                else ["check_in", "check_out"],
                "collected": collected if collected is not None
                else {"city": "大阪"},
                "reason": "missing_required",
            },
        },
    }


class TestTwoHopResume:
    """D2 两跳断片回归：反问后纯槽位值回答必须回原子图。"""

    def test_single_date_answer_resumes_booking(self):
        """核心用例：问「哪天入住？」→ 答「10月3日」→ 回 travel_booking。"""
        update = resolve_booking_pending("10月3日", _routing())
        assert update is not None
        assert update["route_mode"] == "travel_booking"
        assert update["route_decision"] is None

    def test_date_range_answer_resumes(self):
        update = resolve_booking_pending("10月3日到5日", _routing())
        assert update is not None and update["route_mode"] == "travel_booking"

    def test_city_answer_resumes(self):
        """追问缺城市（「住哪个城市？」）→ 答「大阪」→ 回子图。"""
        update = resolve_booking_pending(
            "大阪", _routing(missing=["city", "check_in", "check_out"],
                            collected={}))
        assert update is not None and update["route_mode"] == "travel_booking"

    def test_flight_departure_date_resumes(self):
        """机票缺出发日 → 答单日期 → 回子图（flight 抽取同源）。"""
        update = resolve_booking_pending(
            "10月3日",
            _routing(route_mode="travel_booking", kind="flight",
                     missing=["departure_date"],
                     collected={"origin": "东京", "destination": "大阪"}))
        assert update is not None and update["route_mode"] == "travel_booking"

    def test_commerce_domain_resumes_to_commerce(self):
        """比价域挂起 → 回到 travel_commerce（不回 booking）。"""
        update = resolve_booking_pending(
            "10月3日",
            _routing(active_domain="travel_commerce",
                     route_mode="travel_commerce"))
        assert update is not None and update["route_mode"] == "travel_commerce"

    def test_no_new_slot_does_not_resume(self):
        """换了城市但仍缺日期 → 缺失数量不减 → 不拦（交回 prefilter）。"""
        update = resolve_booking_pending(
            "帮我订一间大阪的酒店",
            _routing(missing=["check_in", "check_out"],
                     collected={"city": "大阪"}))
        assert update is None


class TestGuardConditions:
    def test_no_active_transaction_domain_never_intercepts(self):
        assert resolve_booking_pending("10月3日", _routing(active_domain="travel")) is None
        assert resolve_booking_pending("10月3日", _routing(active_domain="")) is None

    def test_no_booking_intent_never_intercepts(self):
        rt = _routing()
        rt["brief_summary"]["booking_intent"] = None
        assert resolve_booking_pending("10月3日", rt) is None

    def test_incomplete_intent_never_intercepts(self):
        """挂起缺 missing_slots（无结构化缺失）→ 不介入。"""
        rt = _routing()
        rt["brief_summary"]["booking_intent"]["missing_slots"] = []
        assert resolve_booking_pending("10月3日", rt) is None

    def test_cs_strong_signal_passes_through(self):
        """客服强信号放行：「订单退款」不被交易挂起拦截（CS 优先铁律）。"""
        rt = _routing(missing=["check_in", "check_out"],
                      collected={"city": "大阪"})
        assert resolve_booking_pending("我的订单能退款吗", rt) is None

    def test_unrelated_short_query_misses(self):
        assert resolve_booking_pending("谢谢", _routing()) is None

    def test_long_query_not_intercepted(self):
        assert resolve_booking_pending(
            "帮我查一下上周的销售额环比报表数据明细", _routing()) is None

    def test_global_flag_off_via_config(self, monkeypatch):
        import backend.config.travel as T

        monkeypatch.setattr(T, "BOOKING_PENDING_RESUME_ENABLED", False)
        assert resolve_booking_pending("10月3日", _routing()) is None

    def test_domain_disabled_does_not_intervene(self, monkeypatch):
        """域总开关关 → 挂起不该存在，退化即不介入（交正常路由）。"""
        import backend.config.travel_booking as B

        monkeypatch.setattr(B, "is_booking_enabled", lambda: False)
        assert resolve_booking_pending("10月3日", _routing()) is None


class TestG1RouteModeDomainMap:
    """G1 守护：交易两域必须登记，否则 prefilter 命中不回写 active_domain。"""

    def test_transaction_domains_registered(self):
        from backend.orchestration.graph.routing.prefilter_chain import (
            _ROUTE_MODE_DOMAIN,
        )

        assert _ROUTE_MODE_DOMAIN.get("travel_booking") == "travel_booking"
        assert _ROUTE_MODE_DOMAIN.get("travel_commerce") == "travel_commerce"

    def test_routing_package_exports_wrapper(self):
        from backend.orchestration.graph.routing import try_booking_pending

        update = try_booking_pending("10月3日", _routing())
        assert update is not None and update["route_mode"] == "travel_booking"
