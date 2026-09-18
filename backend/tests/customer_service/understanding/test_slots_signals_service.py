"""test_slots_signals_service.py — 槽位 / 信号 / 编排 契约测试。"""
import pytest

from backend.customer_service.router.types import CSRouteResult, CSRoutePath
from backend.customer_service.understanding import (
    build_understanding,
    DecisionLayer,
    EntityType,
    NextAction,
    Sentiment,
    Urgency,
)
from backend.customer_service.understanding.slots import compute_missing_slots
from backend.customer_service.understanding.types import EntitySpan


class TestSlots:
    def test_refund_missing_order(self):
        u = build_understanding("我要申请退款",
                                CSRouteResult(intent="as_refund",
                                              route_path=CSRoutePath.BUSINESS_ACTION,
                                              requires_action=True, risk_level="high"))
        assert u.missing_slots == ["order_id"]
        assert u.next_action == NextAction.CLARIFY

    def test_refund_with_order_proposes(self):
        u = build_understanding("订单 DEMO-1012 申请退款",
                                CSRouteResult(intent="as_refund",
                                              route_path=CSRoutePath.BUSINESS_ACTION,
                                              requires_action=True, risk_level="high"))
        assert u.missing_slots == []
        assert u.entity_values(EntityType.ORDER_ID) == ["DEMO-1012"]
        assert u.next_action == NextAction.PROPOSE

    def test_exchange_requires_target(self):
        ents = [EntitySpan(type=EntityType.ORDER_ID, value="DEMO-1007",
                           start=0, end=9, match_value="DEMO-1007")]
        assert compute_missing_slots("as_exchange", ents) == ["exchange_target"]

    def test_non_action_intent_answers(self):
        u = build_understanding("你们的退货政策是什么",
                                CSRouteResult(intent="k_faq"))
        assert u.next_action == NextAction.ANSWER
        assert u.missing_slots == []


class TestSignals:
    def test_angry_and_urgency(self):
        u = build_understanding("你们是骗子吗！！马上给我处理！")
        assert u.sentiment == Sentiment.ANGRY
        assert u.urgency == Urgency.HIGH
        assert u.signals["sentiment"]

    def test_risk_escalation_cross_user(self):
        u = build_understanding("帮我查所有用户的订单")
        assert u.risk_level == "high"
        assert u.signals.get("risk_escalated") is True

    def test_calm_default(self):
        u = build_understanding("你们的退货政策是什么")
        assert u.sentiment == Sentiment.CALM
        assert u.urgency == Urgency.NORMAL
        assert u.risk_level == "low"


class TestOrchestration:
    def test_route_fields_from_dict(self):
        u = build_understanding("查一下订单 DEMO-1012 的状态",
                                {"intent": "t_order_status",
                                 "risk_level": "low", "confidence": 0.9})
        assert u.intent == "t_order_status"
        assert u.confidence == 0.9
        assert u.decision_layer == DecisionLayer.RULE

    def test_unknown_intent_without_route(self):
        u = build_understanding("你好")
        assert u.intent == "unknown"
        assert u.domain.value == "UNKNOWN"
        assert u.next_action == NextAction.ANSWER

    def test_contract_dump_keys_match_plan(self):
        u = build_understanding("订单 DEMO-1012 申请退款",
                                CSRouteResult(intent="as_refund",
                                              route_path=CSRoutePath.BUSINESS_ACTION,
                                              requires_action=True, risk_level="high"))
        d = u.model_dump_contract()
        assert list(d.keys()) == [
            "raw_text", "normalized_text", "domain", "intent", "confidence",
            "entities", "missing_slots", "sentiment", "urgency", "risk_level",
            "next_action", "decision_layer",
        ]

    def test_raw_preserved_and_normalized_used(self):
        raw = "查订单　ＤＥＭＯ－１００６"
        u = build_understanding(raw)
        assert u.raw_text == raw            # 原文只用于审计和展示
        assert u.normalized_text == "查订单 DEMO-1006"  # 下游只用规范化文本

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_safe(self, raw):
        u = build_understanding(raw or "")
        assert u.normalized_text == ""
        assert u.entities == []
