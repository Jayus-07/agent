"""tests/rag/test_query_tier_fix.py — 三层查询路由 Tier 判定（D-Q2 修复锁定）

修复（2026-10-05，验收清单 Q2）：Tier1 兜底收窄为「短问 ≤8 字」；
中长问句兜底 hybrid 双路（不再跳过 BM25）；引号/书名号专名直达 hybrid。
背景：专名长问「崇妙保圣坚牢塔在哪里？」原判 vector_only 单腿，
travel 库实测拒答率 70%（专名向量语义弱 + BM25 被跳过）。
"""
from __future__ import annotations

import pytest

from backend.rag.retrieval import hybrid as hybrid_module
from backend.rag.retrieval.hybrid import _classify_query_tier


@pytest.fixture(autouse=True)
def _auto_mode(monkeypatch: pytest.MonkeyPatch):
    """钉回 adaptive 自动判型（防止开发机 env 钉死覆盖判定）。"""
    from backend.config import rag as rag_config

    monkeypatch.setattr(rag_config, "ADAPTIVE_RETRIEVAL_MODE", "auto")


class TestQ2ProperNounFix:
    """修复目标场景：专名长问必须走 hybrid（BM25 不再被跳过）。"""

    @pytest.mark.parametrize("query", [
        "崇妙保圣坚牢塔在哪里？",
        "三坊七巷有什么看点",
        "福州的乌塔建于什么年代",
        # Phase 4（2026-10-07）：单弱信号「怎么」不再触发 3 变体改写，
        # 但必须保住 hybrid 双路（禁落 vector_only，2026-09-13 事故语义）
        "鼓山怎么走",
    ])
    def test_proper_noun_queries_not_vector_only(self, query):
        tier = _classify_query_tier(query)
        assert tier in ("hybrid", "hybrid_multi_query"), (
            f"{query!r} 应走双路，实得 {tier}"
        )
        assert tier != "vector_only"

    def test_quoted_name_goes_hybrid(self):
        """引号/书名号是显式专名标记，直达 hybrid。"""
        assert _classify_query_tier("「三坊七巷」的开放时间") == "hybrid"
        # Phase 4：单弱信号「怎么」→ hybrid（书名号专名规则同样先于弱信号）
        assert _classify_query_tier("《福州旅游攻略》里怎么说") == "hybrid"
        assert _classify_query_tier("查阅《福州市志》") == "hybrid"


class TestTier1ShortFaqPreserved:
    """短问维持 vector_only（简单 FAQ 体验与成本不变）。"""

    @pytest.mark.parametrize("query", [
        "发票抬头是什么",
        "门票多少钱",
        "几点开门",
    ])
    def test_short_queries_stay_vector_only(self, query):
        assert _classify_query_tier(query) == "vector_only"

    def test_operation_single_weak_signal_goes_hybrid(self):
        """Phase 4：单个操作性弱信号（流程）→ hybrid 双路，不再触发改写。"""
        assert _classify_query_tier("退款流程是什么") == "hybrid"


class TestTierPrioritiesUnchanged:
    """既有优先级语义零变化（复杂 > 标识符 > 短问兜底）。"""

    def test_complex_beats_identifier(self):
        # 含「对比」复杂词 + SKU → Tier3 优先（存量语义）
        assert _classify_query_tier("对比 AB-1234 和 CD-5678 的差异") == "hybrid_multi_query"

    def test_identifier_still_hybrid(self):
        assert _classify_query_tier("订单号 A123456789 状态") == "hybrid"

    def test_multi_question_multi_query(self):
        assert _classify_query_tier("门票多少钱？几点开门？") == "hybrid_multi_query"

    def test_adaptive_mode_override(self, monkeypatch: pytest.MonkeyPatch):
        from backend.config import rag as rag_config

        monkeypatch.setattr(rag_config, "ADAPTIVE_RETRIEVAL_MODE", "vector_only")
        assert _classify_query_tier("任何问句") == "vector_only"


class TestPhase4SignalScore:
    """Phase 4（2026-10-07）signal-score 分级：昂贵 MQ 只在真正多意图时触发。

    目标路由（spec §22）：Tier1 简单 FAQ → vector_only；
    Tier2 普通单意图/专名/流程类单问 → hybrid；
    Tier3 multi-intent/对比/multi-hop → hybrid_multi_query。
    """

    @pytest.mark.parametrize("query", [
        "报销流程怎么走？",          # 任务书 §21 原例：单弱信号
        "怎么申请退货",              # 单弱信号
        "库存盘点流程是什么",        # 单弱信号
        "差评处理方法有哪些",        # 单弱信号
    ])
    def test_single_weak_signal_not_multi_query(self, query):
        assert _classify_query_tier(query) == "hybrid"

    @pytest.mark.parametrize("query", [
        "退货流程是什么，怎么操作",      # 流程 + 怎么，跨子句（弱信号叠加）
        "请分析一下上季度销售额下滑的原因以及应对方法",  # 分析+原因+方法+以及，长句
        "退款的原因和步骤分别是什么",    # 原因 + 步骤 + 分别(强)
    ])
    def test_weak_signal_combo_triggers_multi_query(self, query):
        assert _classify_query_tier(query) == "hybrid_multi_query"

    def test_short_single_clause_weak_combo_stays_hybrid(self):
        """任务书 §21 原例：单子句短问里 流程+怎么 天然共现 = 单一意图。"""
        assert _classify_query_tier("报销流程怎么走？") == "hybrid"

    @pytest.mark.parametrize("query", [
        "对比 AB-1234 和 CD-5678 的差异",
        "两个方案的优缺点分别是什么",
        "A 和 B 哪个更划算",
    ])
    def test_strong_signal_triggers_multi_query(self, query):
        assert _classify_query_tier(query) == "hybrid_multi_query"

    def test_long_query_with_connective_triggers_multi_query(self):
        # §21 强信号：长 query + 多意图连接词
        long_q = (
            "请帮我梳理一下这个季度店铺在流量转化、客单价以及"
            "售后退款率三个维度的表现，并给出对应的改进建议"
        )
        assert _classify_query_tier(long_q) == "hybrid_multi_query"

    def test_weak_signal_never_falls_to_vector_only(self):
        """2026-09-13 事故语义保留：流程类问题禁止落 vector_only 跳过 BM25。"""
        assert _classify_query_tier("退货流程怎么走") != "vector_only"

    def test_complex_pattern_table_union_preserved(self):
        """G2：聚合别名 = 强 ∪ 弱，旧消费方可见集不缩水。"""
        assert set(hybrid_module.COMPLEX_PATTERNS) == (
            set(hybrid_module.COMPLEX_STRONG_PATTERNS)
            | set(hybrid_module.COMPLEX_WEAK_PATTERNS)
        )

    def test_need_multi_query_follows_tier(self):
        """need_multi_query 唯一入口与分类器同语义。"""
        from backend.rag.retrieval import multi_query as mq

        orig = mq._mq_mode
        mq._mq_mode = "auto"
        try:
            assert mq.need_multi_query("报销流程怎么走？")[0] is False
            assert mq.need_multi_query("退货流程是什么，怎么操作")[0] is True
        finally:
            mq._mq_mode = orig
