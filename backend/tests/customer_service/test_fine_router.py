"""test_fine_router.py — CSFineRouter 按域分流测试"""
from unittest.mock import MagicMock

from backend.customer_service.router.fine_router import CSFineRouter
from backend.customer_service.router.types import CSDomain


def _make_fine_with_mocks(vector_result=None):
    """构造跳过 Chroma 初始化的 CSFineRouter。"""
    fr = CSFineRouter.__new__(CSFineRouter)
    fr._collection = None
    if vector_result is not None:
        fr._vector_classify = MagicMock(return_value=vector_result)
    return fr


class TestRuleClassify:
    def test_knowledge_policy_match(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._rule_classify("退货政策是什么", "KNOWLEDGE")
        assert intent == "k_policy"
        assert conf >= 0.5

    def test_complaint_match(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._rule_classify("我要投诉", "COMPLAINT")
        assert intent == "c_complaint"
        assert conf >= 0.5

    def test_human_handoff_match(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._rule_classify("转人工客服", "HUMAN")
        assert intent == "h_handoff"
        assert conf >= 0.5

    def test_no_match_returns_zero(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._rule_classify("今天天气不错", "KNOWLEDGE")
        assert conf == 0.0

    def test_unknown_domain_returns_zero(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._rule_classify("查订单", "TRANSACTION")
        assert conf == 0.0


class TestVectorClassify:
    def test_no_collection_returns_default(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        fr._collection = None
        intent, conf, reason = fr._vector_classify("test", CSDomain.TRANSACTION)
        assert intent == "k_faq"
        assert conf == 0.0

    def test_exception_returns_default(self):
        fr = CSFineRouter.__new__(CSFineRouter)
        mock_collection = MagicMock()
        mock_collection._collection.count.return_value = 1
        mock_collection.similarity_search_with_score.side_effect = RuntimeError("fail")
        fr._collection = mock_collection
        intent, conf, reason = fr._vector_classify("test", CSDomain.TRANSACTION)
        assert intent == "k_faq"
        assert conf == 0.0


class TestClassifyCascade:
    def test_knowledge_rule_decides(self):
        fr = _make_fine_with_mocks(vector_result=("k_faq", 0.0, "unused"))
        intent, conf, reason = fr.classify("退货政策是什么", CSDomain.KNOWLEDGE)
        assert intent == "k_policy"
        assert "rule" in reason

    def test_complaint_rule_decides(self):
        fr = _make_fine_with_mocks(vector_result=("k_faq", 0.0, "unused"))
        intent, conf, reason = fr.classify("我要投诉不满意", CSDomain.COMPLAINT)
        assert intent == "c_complaint"
        assert "rule" in reason

    def test_human_rule_decides(self):
        fr = _make_fine_with_mocks(vector_result=("k_faq", 0.0, "unused"))
        intent, conf, reason = fr.classify("转人工客服", CSDomain.HUMAN)
        assert intent == "h_handoff"
        assert "rule" in reason

    def test_transaction_uses_vector(self):
        fr = _make_fine_with_mocks(
            vector_result=("t_order_status", 0.85, "top=t_order_status(0.85)")
        )
        intent, conf, reason = fr.classify("我的订单到哪了", CSDomain.TRANSACTION)
        assert intent == "t_order_status"
        assert "vector" in reason

    def test_aftersales_uses_vector(self):
        fr = _make_fine_with_mocks(
            vector_result=("as_refund", 0.80, "top=as_refund(0.80)")
        )
        intent, conf, reason = fr.classify("怎么退款", CSDomain.AFTER_SALES)
        assert intent == "as_refund"
        assert "vector" in reason

    def test_account_uses_vector(self):
        fr = _make_fine_with_mocks(
            vector_result=("a_password", 0.75, "top=a_password(0.75)")
        )
        intent, conf, reason = fr.classify("修改密码", CSDomain.ACCOUNT)
        assert intent == "a_password"
        assert "vector" in reason

    def test_fallback_to_default(self):
        fr = _make_fine_with_mocks(
            vector_result=("k_faq", 0.2, "low_confidence")
        )
        intent, conf, reason = fr.classify("模糊查询", CSDomain.TRANSACTION)
        assert intent == "t_order_status"
        assert "default_fallback" in reason

    def test_unknown_domain_fallback(self):
        fr = _make_fine_with_mocks(
            vector_result=("k_faq", 0.2, "low")
        )
        intent, conf, reason = fr.classify("test", CSDomain.UNKNOWN)
        assert intent == "k_faq"
        assert "default_fallback" in reason

    def test_rule_below_threshold_falls_to_vector(self):
        fr = _make_fine_with_mocks(
            vector_result=("t_logistics", 0.70, "top=t_logistics(0.70)")
        )
        intent, conf, reason = fr.classify("一个不相关的词", CSDomain.KNOWLEDGE)
        assert intent == "t_logistics"
        assert "vector" in reason
