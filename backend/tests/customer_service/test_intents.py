"""test_intents.py — 意图定义、KB 映射、路由路径解析测试"""
import pytest

from backend.customer_service.router.intents import (
    FINE_INTENTS,
    INTENT_PROFILES,
    INTENT_KB_MAP,
    DOMAIN_DEFAULT_INTENT,
    resolve_route_path,
    kb_ids_for,
)
from backend.customer_service.router.types import CSDomain, CSRoutePath


class TestFineIntents:
    def test_all_intents_have_profiles(self):
        for intent_id in FINE_INTENTS:
            assert intent_id in INTENT_PROFILES, f"Intent {intent_id} missing from INTENT_PROFILES"

    def test_profile_intent_matches_key(self):
        for intent_id, profile in INTENT_PROFILES.items():
            assert profile.intent == intent_id

    def test_all_domains_represented(self):
        domains = {p.domain for p in INTENT_PROFILES.values()}
        assert CSDomain.KNOWLEDGE in domains
        assert CSDomain.TRANSACTION in domains
        assert CSDomain.AFTER_SALES in domains
        assert CSDomain.ACCOUNT in domains
        assert CSDomain.COMPLAINT in domains
        assert CSDomain.HUMAN in domains


class TestIntentKBMap:
    def test_all_intents_mapped(self):
        for intent_id in INTENT_PROFILES:
            assert intent_id in INTENT_KB_MAP

    def test_kb_ids_are_lists(self):
        for kb_ids in INTENT_KB_MAP.values():
            assert isinstance(kb_ids, list)

    def test_knowledge_intents_have_kbs(self):
        for intent_id, profile in INTENT_PROFILES.items():
            if profile.route_path == CSRoutePath.KNOWLEDGE_QUERY:
                assert len(profile.kb_ids) > 0, f"Knowledge intent {intent_id} has no KBs"


class TestResolveRoutePath:
    def test_known_intents(self):
        assert resolve_route_path("as_refund") == CSRoutePath.BUSINESS_ACTION
        assert resolve_route_path("k_faq") == CSRoutePath.KNOWLEDGE_QUERY
        assert resolve_route_path("h_handoff") == CSRoutePath.HUMAN_HANDOFF
        assert resolve_route_path("c_complaint") == CSRoutePath.COMPLAINT_FLOW

    def test_unknown_intent_fallback(self):
        assert resolve_route_path("nonexistent") == CSRoutePath.KNOWLEDGE_QUERY


class TestKbIdsFor:
    def test_known_intent(self):
        kbs = kb_ids_for("as_refund")
        assert "cs_policy" in kbs
        assert "cs_aftersales" in kbs

    def test_unknown_intent_with_domain(self):
        kbs = kb_ids_for("unknown_intent", domain=CSDomain.KNOWLEDGE)
        assert len(kbs) > 0

    def test_unknown_intent_no_domain(self):
        kbs = kb_ids_for("unknown_intent")
        assert kbs == ["cs_faq"]

    def test_domain_string(self):
        kbs = kb_ids_for("unknown", domain="AFTER_SALES")
        assert len(kbs) > 0


class TestDomainDefaultIntent:
    def test_all_domains_have_default(self):
        for domain in CSDomain:
            if domain != CSDomain.UNKNOWN:
                assert domain.value in DOMAIN_DEFAULT_INTENT

    def test_defaults_exist_in_profiles(self):
        for intent_id in DOMAIN_DEFAULT_INTENT.values():
            assert intent_id in INTENT_PROFILES
