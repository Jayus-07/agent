"""test_coarse_router.py — CSCoarseRouter 纯规则 fallback 测试

（2026-09-17 删除 Chroma 向量层：Rule → Hint → UNKNOWN）
"""
from backend.customer_service.router.coarse_router import CSCoarseRouter
from backend.customer_service.router.types import CSDomain


class TestRuleClassify:
    def test_three_keyword_hits_decides(self):
        cr = CSCoarseRouter()
        keywords = {
            "TRANSACTION": ["订单", "物流", "快递", "发货"],
            "AFTER_SALES": ["退款", "退货", "换货"],
        }
        domain, conf, reason = cr._rule_classify("查一下订单物流快递", keywords)
        assert domain == CSDomain.TRANSACTION
        assert conf >= 0.8

    def test_one_hit_not_enough(self):
        cr = CSCoarseRouter()
        keywords = {
            "TRANSACTION": ["订单", "物流", "快递", "发货"],
        }
        domain, conf, reason = cr._rule_classify("查一下订单", keywords)
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0

    def test_no_match(self):
        cr = CSCoarseRouter()
        keywords = {"TRANSACTION": ["订单", "物流"]}
        domain, conf, reason = cr._rule_classify("今天天气不错", keywords)
        assert domain == CSDomain.UNKNOWN


class TestCascadeLogic:
    def test_rule_decides_first(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流", "快递"]},
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("查订单物流快递")
        assert domain == CSDomain.TRANSACTION
        assert "rule" in reason

    def test_rule_single_domain_pair(self, monkeypatch):
        """两域各 1 hit 不满足 ≥2 同域计数 → UNKNOWN。"""
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"], "AFTER_SALES": ["退款", "退货"]},
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("订单退款了")
        # "订单"→TRANSACTION 1hit，"退款"→AFTER_SALES 1hit，均不足 2
        assert domain == CSDomain.UNKNOWN

    def test_hint_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"]},
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("模糊查询", rule_hint=CSDomain.ACCOUNT)
        assert domain == CSDomain.ACCOUNT
        assert conf == 0.5
        assert "hint_fallback" in reason

    def test_hint_ignored_when_rule_decides(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流", "快递"]},
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("查订单物流快递", rule_hint=CSDomain.ACCOUNT)
        assert domain == CSDomain.TRANSACTION
        assert "rule" in reason

    def test_no_match_returns_unknown(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流"]},
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("模糊查询")
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0
