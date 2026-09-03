"""test_types.py — CS Router 类型定义测试"""
import pytest

from backend.customer_service.router.types import (
    CSDomain,
    CSRoutePath,
    CSDetection,
    CSRouteResult,
    IntentProfile,
)


class TestCSDomain:
    def test_all_seven_values(self):
        values = {d.value for d in CSDomain}
        assert values == {"KNOWLEDGE", "TRANSACTION", "AFTER_SALES", "ACCOUNT", "COMPLAINT", "HUMAN", "UNKNOWN"}

    def test_string_enum(self):
        assert CSDomain.KNOWLEDGE == "KNOWLEDGE"
        assert CSDomain("TRANSACTION") is CSDomain.TRANSACTION


class TestCSRoutePath:
    def test_all_five_values(self):
        values = {p.value for p in CSRoutePath}
        assert values == {"knowledge_query", "business_query", "business_action", "complaint_flow", "human_handoff"}


class TestCSDetection:
    def test_defaults(self):
        d = CSDetection()
        assert d.is_cs is False
        assert d.rule_hits == []
        assert d.rule_score == 0.0
        assert d.vector_score == 0.0
        assert d.reason is None

    def test_custom_values(self):
        d = CSDetection(is_cs=True, rule_hits=["投诉", "差评"], rule_score=0.9, vector_score=0.8, reason="test")
        assert d.is_cs is True
        assert len(d.rule_hits) == 2


class TestCSRouteResult:
    def test_defaults(self):
        r = CSRouteResult()
        assert r.domain == CSDomain.UNKNOWN
        assert r.intent == "unknown"
        assert r.confidence == 0.0
        assert r.requires_auth is False
        assert r.route_path == CSRoutePath.KNOWLEDGE_QUERY
        assert r.kb_ids == []

    def test_custom(self):
        r = CSRouteResult(
            domain=CSDomain.AFTER_SALES, intent="as_refund",
            confidence=0.9, requires_auth=True, requires_action=True,
            risk_level="high", route_path=CSRoutePath.BUSINESS_ACTION,
            kb_ids=["cs_policy", "cs_aftersales"],
        )
        assert r.domain == CSDomain.AFTER_SALES
        assert r.risk_level == "high"
        assert len(r.kb_ids) == 2


class TestIntentProfile:
    def test_defaults(self):
        p = IntentProfile(intent="k_faq", domain=CSDomain.KNOWLEDGE)
        assert p.requires_auth is False
        assert p.requires_action is False
        assert p.risk_level == "low"
        assert p.route_path == CSRoutePath.KNOWLEDGE_QUERY
        assert p.kb_ids == []
