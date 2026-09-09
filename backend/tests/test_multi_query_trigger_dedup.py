"""MultiQuery 触发与去重优化测试（P1-6/P1-7）。

背景（2026-09-03 事故）：
- "退款审核时间是多少？"因业务关键词"退款"强制触发 LLM 改写，3 个近义
  变体各自走完整检索管线，产生 13 次重复检索；
- 字符级 Jaccard 对中文短问句区分度不足，语义等价变体未被去重。

修复：
- 简单事实问句（…是多少/多久/几天…）即使命中业务关键词也豁免改写；
- 变体去重升级为词级（jieba）Jaccard，jieba 缺失时回退字符级。
"""
import pytest


class TestSimpleFactExemption:
    """P1-7：简单事实问句豁免改写（即使含业务关键词）。"""

    def _need(self, query: str) -> bool:
        from backend.rag.retrieval import multi_query as mq

        # 固定 auto 模式，避免受运行时 set_mq_mode 影响
        orig = mq._mq_mode
        mq._mq_mode = "auto"
        try:
            use, _ = mq.need_multi_query(query)
            return use
        finally:
            mq._mq_mode = orig

    def test_business_keyword_fact_question_not_rewritten(self):
        assert self._need("退款审核时间是多少？") is False

    def test_business_keyword_duration_question_not_rewritten(self):
        assert self._need("退款多久到账") is False

    def test_business_keyword_process_question_still_rewritten(self):
        """含复杂度模式（流程/怎么）的业务问题仍应改写。"""
        assert self._need("退款流程是什么，怎么操作") is True

    def test_plain_business_question_still_rewritten(self):
        """非事实问句的业务问题保持既有触发行为。"""
        assert self._need("差评怎么处理比较好呢") is True


class TestWordLevelDedup:
    """P1-6：变体去重升级为词级（jieba）相似度 + 词集包含度判定。"""

    def test_subset_variant_deduped(self):
        """添加虚词的变体：字符级 0.9 不严格大于阈值会保留，
        词级包含度 = 1.0 应去重（判别性用例）。"""
        from backend.rag.retrieval import multi_query as mq

        original = "退款审核时间是多少"
        # 词集 ⊂ 原查询词集 ∪ {的}，仅多一个虚词 → 无新检索价值
        result = mq._dedup(["退款审核的时间是多少"], original)

        assert result == [original]

    def test_word_order_variant_deduped(self):
        """词序变换变体（回归保护：字符级本就能去掉）。"""
        from backend.rag.retrieval import multi_query as mq

        original = "退款审核时间是多少"
        result = mq._dedup(["审核退款时间是多少"], original)

        assert result == [original]

    def test_distinct_variant_kept(self):
        """语义明显不同的变体应保留。"""
        from backend.rag.retrieval import multi_query as mq

        original = "退款审核时间是多少"
        lines = ["客户投诉物流慢怎么安抚"]
        result = mq._dedup(lines, original)

        assert result[0] == original
        assert "客户投诉物流慢怎么安抚" in result

    def test_exact_duplicate_removed(self):
        from backend.rag.retrieval import multi_query as mq

        result = mq._dedup(["退款审核时间是多少", "其他问题"], "退款审核时间是多少")
        assert result == ["退款审核时间是多少", "其他问题"]
