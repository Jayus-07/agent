"""test_cs_router.py — CS Router 门面测试"""
import pytest
from unittest.mock import MagicMock, patch

from backend.customer_service.router.types import (
    CSDetection,
    CSDomain,
    CSRoutePath,
    CSRouteResult,
)
from backend.customer_service.router.cs_router import CSRouter


def _make_router_with_mocks(coarse_result, fine_result):
    """构造 CSRouter，绕过 Chroma 初始化。"""
    router = CSRouter.__new__(CSRouter)
    router.coarse = MagicMock()
    router.fine = MagicMock()
    router.coarse.classify.return_value = coarse_result
    router.fine.classify.return_value = fine_result
    return router


class TestCSRouterRoute:
    def test_knowledge_intent_full_pipeline(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.KNOWLEDGE, 0.9, "rule: KNOWLEDGE(3hits)"),
            fine_result=("k_policy", 0.8, "rule: k_policy(2hits)"),
        )
        detection = CSDetection(
            is_cs=True, rule_hits=["KNOWLEDGE", "KNOWLEDGE"],
            rule_score=0.9, vector_score=0.8,
        )
        result = router.route("请问退货政策是什么", detection=detection)

        assert result.domain == CSDomain.KNOWLEDGE
        assert result.intent == "k_policy"
        assert result.route_path == CSRoutePath.KNOWLEDGE_QUERY
        assert result.confidence == pytest.approx(0.85, abs=0.01)
        assert result.requires_auth is False
        assert "cs_policy" in result.kb_ids

    def test_aftersales_intent_requires_auth(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.AFTER_SALES, 0.9, "rule: AFTER_SALES(3hits)"),
            fine_result=("as_refund", 0.9, "rule: as_refund(2hits)"),
        )
        result = router.route("我要退款退货")

        assert result.domain == CSDomain.AFTER_SALES
        assert result.intent == "as_refund"
        assert result.requires_auth is True
        assert result.requires_action is True
        assert result.risk_level == "high"
        assert result.route_path == CSRoutePath.BUSINESS_ACTION

    def test_human_handoff_no_kb(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.HUMAN, 0.9, "rule: HUMAN(3hits)"),
            fine_result=("h_handoff", 1.0, "rule: h_handoff(2hits)"),
        )
        result = router.route("转人工客服")

        assert result.domain == CSDomain.HUMAN
        assert result.intent == "h_handoff"
        assert result.route_path == CSRoutePath.HUMAN_HANDOFF
        assert result.kb_ids == []
        assert result.requires_auth is False

    def test_confidence_is_average_of_coarse_and_fine(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.KNOWLEDGE, 0.6, "vector: KNOWLEDGE(0.60)"),
            fine_result=("k_faq", 0.8, "vector: k_faq(0.80)"),
        )
        result = router.route("营业时间")

        assert result.confidence == pytest.approx(0.7, abs=0.01)

    def test_no_detection_extracts_no_hint(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.KNOWLEDGE, 0.5, "vector: KNOWLEDGE(0.50)"),
            fine_result=("k_faq", 0.5, "vector: k_faq(0.50)"),
        )
        result = router.route("有什么活动", detection=None)

        router.coarse.classify.assert_called_once_with("有什么活动", None)
        assert isinstance(result, CSRouteResult)

    def test_reason_combines_coarse_and_fine(self):
        router = _make_router_with_mocks(
            coarse_result=(CSDomain.TRANSACTION, 0.7, "rule: TRANSACTION(3hits)"),
            fine_result=("t_logistics", 0.8, "vector: t_logistics(0.80)"),
        )
        result = router.route("查一下物流")

        assert "coarse=" in result.reason
        assert "fine=" in result.reason


class TestExtractHint:
    def test_none_detection_returns_none(self):
        router = CSRouter.__new__(CSRouter)
        assert router._extract_hint(None) is None

    def test_empty_hits_returns_none(self):
        router = CSRouter.__new__(CSRouter)
        detection = CSDetection(is_cs=True, rule_hits=[], rule_score=0.5)
        assert router._extract_hint(detection) is None

    def test_majority_domain_wins(self):
        router = CSRouter.__new__(CSRouter)
        detection = CSDetection(
            is_cs=True,
            rule_hits=["KNOWLEDGE", "AFTER_SALES", "KNOWLEDGE"],
            rule_score=0.8,
        )
        hint = router._extract_hint(detection)
        assert hint == CSDomain.KNOWLEDGE


class TestErrorFallback:
    def test_coarse_exception_returns_unknown(self):
        router = CSRouter.__new__(CSRouter)
        router.coarse = MagicMock()
        router.fine = MagicMock()
        router.coarse.classify.side_effect = RuntimeError("boom")

        result = router.route("test query")

        assert result.domain == CSDomain.UNKNOWN
        assert result.intent == "unknown"
        assert result.confidence == 0.0
        assert "error" in result.reason

    def test_fine_exception_returns_unknown(self):
        router = CSRouter.__new__(CSRouter)
        router.coarse = MagicMock()
        router.fine = MagicMock()
        router.coarse.classify.return_value = (CSDomain.KNOWLEDGE, 0.9, "ok")
        router.fine.classify.side_effect = RuntimeError("boom")

        result = router.route("test query")

        assert result.domain == CSDomain.UNKNOWN
        assert result.intent == "unknown"
