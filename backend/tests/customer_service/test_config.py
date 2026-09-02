"""test_config.py — 客服配置模块测试"""
import re

from backend.config.customer_service import (
    CS_ENABLED,
    CS_CONFIRMATION_TTL_SECONDS,
    CS_HANDOFF_TIMEOUT_SECONDS,
    CS_HANDOFF_LOW_CONF_THRESHOLD,
    CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT,
    CS_MAX_CONFIRMATION_RETRIES,
    CS_HIGH_RISK_ACTIONS,
    CS_CRITICAL_ACTIONS,
    CS_KNOWLEDGE_BASES,
    COMPLAINT_PATTERNS,
    CS_DOMAIN_KEYWORDS,
)


class TestToggle:
    def test_cs_enabled_is_bool(self):
        assert isinstance(CS_ENABLED, bool)


class TestConfirmationConfig:
    def test_ttl_positive(self):
        assert CS_CONFIRMATION_TTL_SECONDS > 0

    def test_max_retries_positive(self):
        assert CS_MAX_CONFIRMATION_RETRIES > 0


class TestHandoffConfig:
    def test_timeout_positive(self):
        assert CS_HANDOFF_TIMEOUT_SECONDS > 0

    def test_conf_threshold_range(self):
        assert 0.0 <= CS_HANDOFF_LOW_CONF_THRESHOLD <= 1.0

    def test_consecutive_fail_limit_positive(self):
        assert CS_HANDOFF_CONSECUTIVE_FAIL_LIMIT > 0


class TestRiskActions:
    def test_high_risk_is_frozenset(self):
        assert isinstance(CS_HIGH_RISK_ACTIONS, frozenset)

    def test_critical_is_frozenset(self):
        assert isinstance(CS_CRITICAL_ACTIONS, frozenset)

    def test_high_risk_contains_expected(self):
        assert "refund_execute" in CS_HIGH_RISK_ACTIONS
        assert "order_cancel" in CS_HIGH_RISK_ACTIONS

    def test_critical_contains_expected(self):
        assert "account_delete" in CS_CRITICAL_ACTIONS
        assert "bulk_refund" in CS_CRITICAL_ACTIONS

    def test_no_overlap(self):
        assert CS_HIGH_RISK_ACTIONS & CS_CRITICAL_ACTIONS == set()


class TestKnowledgeBases:
    def test_six_bases_defined(self):
        assert len(CS_KNOWLEDGE_BASES) == 6

    def test_each_has_name_and_description(self):
        for kb_id, meta in CS_KNOWLEDGE_BASES.items():
            assert "name" in meta, f"{kb_id} missing 'name'"
            assert "description" in meta, f"{kb_id} missing 'description'"

    def test_expected_kb_ids(self):
        expected = {"cs_faq", "cs_product", "cs_policy", "cs_aftersales", "cs_complaint", "cs_scripts"}
        assert set(CS_KNOWLEDGE_BASES.keys()) == expected


class TestComplaintPatterns:
    def test_patterns_are_compiled(self):
        for p in COMPLAINT_PATTERNS:
            assert isinstance(p, re.Pattern)

    def test_detects_complaint_keyword(self):
        text = "我要投诉你们的服务"
        assert any(p.search(text) for p in COMPLAINT_PATTERNS)

    def test_no_false_positive_on_normal_query(self):
        text = "请问如何查询订单状态"
        assert not any(p.search(text) for p in COMPLAINT_PATTERNS)


class TestDomainKeywords:
    def test_six_domains(self):
        expected = {"KNOWLEDGE", "TRANSACTION", "AFTER_SALES", "ACCOUNT", "COMPLAINT", "HUMAN"}
        assert set(CS_DOMAIN_KEYWORDS.keys()) == expected

    def test_each_domain_has_keywords(self):
        for domain, kws in CS_DOMAIN_KEYWORDS.items():
            assert len(kws) > 0, f"{domain} has no keywords"
