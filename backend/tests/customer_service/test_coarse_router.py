"""test_coarse_router.py — CSCoarseRouter 3 层 fallback 测试"""
from unittest.mock import MagicMock

import pytest

from backend.customer_service.router.coarse_router import CSCoarseRouter
from backend.customer_service.router.types import CSDomain


def _make_coarse_with_mocks(rule_result=None, vector_result=None):
    """构造跳过 Chroma 初始化的 CSCoarseRouter，注入通道结果。"""
    cr = CSCoarseRouter.__new__(CSCoarseRouter)
    cr._collection = None
    if rule_result is not None:
        cr._rule_classify = MagicMock(return_value=rule_result)
    if vector_result is not None:
        cr._vector_classify = MagicMock(return_value=vector_result)
    return cr


class TestRuleClassify:
    def test_three_keyword_hits_decides(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        cr._collection = None
        keywords = {
            "TRANSACTION": ["订单", "物流", "快递", "发货"],
            "AFTER_SALES": ["退款", "退货", "换货"],
        }
        domain, conf, reason = cr._rule_classify("查一下订单物流快递", keywords)
        assert domain == CSDomain.TRANSACTION
        assert conf >= 0.8

    def test_one_hit_not_enough(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        cr._collection = None
        keywords = {
            "TRANSACTION": ["订单", "物流", "快递", "发货"],
        }
        domain, conf, reason = cr._rule_classify("查一下订单", keywords)
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0

    def test_no_match(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        cr._collection = None
        keywords = {"TRANSACTION": ["订单", "物流"]}
        domain, conf, reason = cr._rule_classify("今天天气不错", keywords)
        assert domain == CSDomain.UNKNOWN


class TestVectorClassify:
    def test_no_collection_returns_unknown(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        cr._collection = None
        domain, conf, reason = cr._vector_classify("test")
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0

    def test_high_similarity_decides(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        mock_doc = MagicMock()
        mock_doc.metadata = {"domain": "AFTER_SALES"}
        mock_collection = MagicMock()
        mock_collection._collection.count.return_value = 10
        mock_collection.similarity_search_with_score.return_value = [
            (mock_doc, 0.1),
        ]
        cr._collection = mock_collection
        domain, conf, reason = cr._vector_classify("怎么退款")
        assert domain == CSDomain.AFTER_SALES
        assert conf > 0.85

    def test_exception_returns_unknown(self):
        cr = CSCoarseRouter.__new__(CSCoarseRouter)
        mock_collection = MagicMock()
        mock_collection._collection.count.return_value = 1
        mock_collection.similarity_search_with_score.side_effect = RuntimeError("fail")
        cr._collection = mock_collection
        domain, conf, reason = cr._vector_classify("test")
        assert domain == CSDomain.UNKNOWN


class TestCascadeLogic:
    def test_rule_decides_first(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流", "快递"]},
        )
        cr = _make_coarse_with_mocks(
            rule_result=(CSDomain.TRANSACTION, 0.9, "TRANSACTION(3hits)"),
            vector_result=(CSDomain.AFTER_SALES, 0.95, "should_not_reach"),
        )
        domain, conf, reason = cr.classify("查订单物流快递")
        assert domain == CSDomain.TRANSACTION
        assert "rule" in reason

    def test_vector_decides_when_rule_weak(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流", "快递"]},
        )
        cr = _make_coarse_with_mocks(
            rule_result=(CSDomain.UNKNOWN, 0.0, ""),
            vector_result=(CSDomain.AFTER_SALES, 0.90, "top=AFTER_SALES(0.90)"),
        )
        domain, conf, reason = cr.classify("退款怎么办")
        assert domain == CSDomain.AFTER_SALES
        assert "vector" in reason

    def test_vector_moderate_accepted(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"]},
        )
        cr = _make_coarse_with_mocks(
            rule_result=(CSDomain.UNKNOWN, 0.0, ""),
            vector_result=(CSDomain.KNOWLEDGE, 0.70, "top=KNOWLEDGE(0.70)"),
        )
        domain, conf, reason = cr.classify("请问一下")
        assert domain == CSDomain.KNOWLEDGE
        assert "vector_moderate" in reason

    def test_hint_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"]},
        )
        cr = _make_coarse_with_mocks(
            rule_result=(CSDomain.UNKNOWN, 0.0, ""),
            vector_result=(CSDomain.UNKNOWN, 0.3, "low"),
        )
        domain, conf, reason = cr.classify("模糊查询", rule_hint=CSDomain.ACCOUNT)
        assert domain == CSDomain.ACCOUNT
        assert conf == 0.5
        assert "hint_fallback" in reason

    def test_no_match_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"]},
        )
        cr = _make_coarse_with_mocks(
            rule_result=(CSDomain.UNKNOWN, 0.0, ""),
            vector_result=(CSDomain.UNKNOWN, 0.3, "low"),
        )
        domain, conf, reason = cr.classify("模糊查询")
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0
