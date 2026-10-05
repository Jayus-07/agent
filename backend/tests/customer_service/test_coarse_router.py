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
        domain, conf, reason = cr._rule_classify(
            "查一下订单物流快递", keywords, patterns={}
        )
        assert domain == CSDomain.TRANSACTION
        assert conf >= 0.8

    def test_one_hit_not_enough(self):
        cr = CSCoarseRouter()
        keywords = {
            "TRANSACTION": ["订单", "物流", "快递", "发货"],
        }
        domain, conf, reason = cr._rule_classify(
            "查一下订单", keywords, patterns={}
        )
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0

    def test_no_match(self):
        cr = CSCoarseRouter()
        keywords = {"TRANSACTION": ["订单", "物流"]}
        domain, conf, reason = cr._rule_classify(
            "今天天气不错", keywords, patterns={}
        )
        assert domain == CSDomain.UNKNOWN

    def test_pattern_hit_counts_toward_votes(self):
        """P3.5：正则模式与关键词同权计票——单关键词+单模式命中即决定。"""
        import re

        cr = CSCoarseRouter()
        keywords = {"AFTER_SALES": ["退款"]}
        patterns = {"AFTER_SALES": [re.compile(r"申请(退款|退货|换货)")]}
        domain, conf, reason = cr._rule_classify(
            "我要给订单 DEMO-1002 申请退款", keywords, patterns=patterns
        )
        assert domain == CSDomain.AFTER_SALES
        assert conf >= 2 / 3

    def test_after_sales_policy_question_prefers_knowledge_domain(self):
        cr = CSCoarseRouter()
        domain, _, _ = cr.classify("七天无理由退货的范围包括哪些商品")

        assert domain == CSDomain.KNOWLEDGE

    def test_explicit_after_sales_action_keeps_action_domain(self):
        cr = CSCoarseRouter()
        domain, _, _ = cr.classify("帮我申请退款")

        assert domain == CSDomain.AFTER_SALES

    def test_after_sales_question_forms_are_knowledge(self):
        cr = CSCoarseRouter()
        questions = (
            "什么商品不支持七天无理由退货",
            "退货运费谁来承担",
            "赠品需要一起退回吗",
            "退货商品的钱什么时候退给我",
            "退货运费险怎么理赔",
        )

        assert all(cr.classify(query)[0] == CSDomain.KNOWLEDGE for query in questions)

    def test_delivery_policy_questions_are_knowledge(self):
        cr = CSCoarseRouter()

        assert cr.classify("发欧洲多久能到")[0] == CSDomain.KNOWLEDGE
        assert cr.classify("跨国包邮的包裹通常几天送达")[0] == CSDomain.KNOWLEDGE

    def test_order_tracking_questions_remain_transactional(self):
        cr = CSCoarseRouter()

        assert cr.classify("查我的包裹什么时候到")[0] == CSDomain.TRANSACTION

    def test_quality_complaint_does_not_fall_into_order_query(self):
        cr = CSCoarseRouter()

        domain, _, reason = cr.classify("这个包裹签收了但商品坏了要投诉")

        assert domain == CSDomain.COMPLAINT
        assert "quality_complaint" in reason


class TestCascadeLogic:
    def test_rule_decides_first(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_KEYWORDS",
            {"TRANSACTION": ["订单", "物流", "快递"]},
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_PATTERNS", {}
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
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_PATTERNS", {}
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
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_PATTERNS", {}
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
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_PATTERNS", {}
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
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DOMAIN_PATTERNS", {}
        )
        cr = CSCoarseRouter()
        domain, conf, reason = cr.classify("模糊查询")
        assert domain == CSDomain.UNKNOWN
        assert conf == 0.0
