"""test_seeds.py — 种子语料结构测试"""
import pytest

from backend.customer_service.router.seeds import (
    CS_DOMAIN_SEEDS,
    CS_COARSE_SEEDS,
    CS_FINE_SEEDS,
)
from backend.customer_service.router.intents import FINE_INTENTS


class TestDomainSeeds:
    def test_all_domains_present(self):
        expected = {"KNOWLEDGE", "TRANSACTION", "AFTER_SALES", "ACCOUNT", "COMPLAINT", "HUMAN"}
        assert set(CS_DOMAIN_SEEDS.keys()) == expected

    def test_non_empty(self):
        for domain, seeds in CS_DOMAIN_SEEDS.items():
            assert len(seeds) >= 3, f"Domain {domain} has too few seeds"


class TestCoarseSeeds:
    def test_all_domains_present(self):
        expected = {"KNOWLEDGE", "TRANSACTION", "AFTER_SALES", "ACCOUNT", "COMPLAINT", "HUMAN"}
        assert set(CS_COARSE_SEEDS.keys()) == expected

    def test_non_empty(self):
        for domain, seeds in CS_COARSE_SEEDS.items():
            assert len(seeds) >= 3, f"Domain {domain} has too few coarse seeds"


class TestFineSeeds:
    def test_all_intents_covered(self):
        for intent_id in FINE_INTENTS:
            assert intent_id in CS_FINE_SEEDS, f"Intent {intent_id} missing from CS_FINE_SEEDS"

    def test_non_empty(self):
        for intent_id, seeds in CS_FINE_SEEDS.items():
            assert len(seeds) >= 2, f"Intent {intent_id} has too few fine seeds"
