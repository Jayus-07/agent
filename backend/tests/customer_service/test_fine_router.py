"""test_fine_router.py — CSFineRouter 按域分流测试

（2026-09-17 删除 Chroma 向量层：Rule → DOMAIN_DEFAULT_INTENT 默认意图）
"""
from backend.customer_service.router.fine_router import CSFineRouter
from backend.customer_service.router.types import CSDomain


class TestRuleClassify:
    def test_knowledge_policy_match(self):
        fr = CSFineRouter()
        intent, conf, reason = fr._rule_classify("退货政策是什么", "KNOWLEDGE")
        assert intent == "k_policy"
        assert conf >= 0.5

    def test_complaint_match(self):
        fr = CSFineRouter()
        intent, conf, reason = fr._rule_classify("我要投诉", "COMPLAINT")
        assert intent == "c_complaint"
        assert conf >= 0.5

    def test_human_handoff_match(self):
        fr = CSFineRouter()
        intent, conf, reason = fr._rule_classify("转人工客服", "HUMAN")
        assert intent == "h_handoff"
        assert conf >= 0.5

    def test_no_match_returns_zero(self):
        fr = CSFineRouter()
        intent, conf, reason = fr._rule_classify("今天天气不错", "KNOWLEDGE")
        assert conf == 0.0

    def test_unknown_domain_returns_zero(self):
        fr = CSFineRouter()
        intent, conf, reason = fr._rule_classify("查订单", "TRANSACTION")
        assert conf == 0.0


class TestClassifyCascade:
    def test_knowledge_rule_decides(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("退货政策是什么", CSDomain.KNOWLEDGE)
        assert intent == "k_policy"
        assert "rule" in reason

    def test_complaint_rule_decides(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("我要投诉不满意", CSDomain.COMPLAINT)
        assert intent == "c_complaint"
        assert "rule" in reason

    def test_human_rule_decides(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("转人工客服", CSDomain.HUMAN)
        assert intent == "h_handoff"
        assert "rule" in reason

    def test_transaction_falls_to_default(self):
        """TRANSACTION 域无规则映射 → 默认意图（对齐 Supervisor 门槛置信度）。"""
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("我的订单到哪了", CSDomain.TRANSACTION)
        assert intent == "t_order_status"
        assert "default_fallback" in reason
        assert conf > 0.3  # P2.1: 对齐 CS_CONFIDENCE_CAUTIOUS(0.6)

    def test_aftersales_falls_to_default(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("怎么退款", CSDomain.AFTER_SALES)
        assert intent == "as_refund"
        assert "default_fallback" in reason

    def test_account_falls_to_default(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("修改密码", CSDomain.ACCOUNT)
        assert intent == "a_login_issue"
        assert "default_fallback" in reason

    def test_fallback_to_default(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("模糊查询", CSDomain.TRANSACTION)
        assert intent == "t_order_status"
        assert "default_fallback" in reason

    def test_unknown_domain_fallback(self):
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("test", CSDomain.UNKNOWN)
        assert intent == "k_faq"
        assert "default_fallback" in reason

    def test_rule_decides_before_default(self):
        """K/C/H 域规则命中即采信（含单 hit conf=0.5），不再落默认。"""
        fr = CSFineRouter()
        intent, conf, reason = fr.classify("发票", CSDomain.KNOWLEDGE)
        assert intent == "k_faq"
        assert "rule" in reason
        assert conf >= 0.5
