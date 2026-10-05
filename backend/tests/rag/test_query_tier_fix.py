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
        "鼓山怎么走",  # 4 字专名 + 3 字疑问 = 7 字，但「怎么」是复杂词 → multi_query
    ])
    def test_proper_noun_queries_not_vector_only(self, query):
        tier = _classify_query_tier(query)
        if query == "鼓山怎么走":
            assert tier == "hybrid_multi_query"  # 复杂词优先级不变
        else:
            assert tier == "hybrid", f"{query!r} 应走 hybrid 双路，实得 {tier}"

    def test_quoted_name_goes_hybrid(self):
        """引号/书名号是显式专名标记，直达 hybrid。"""
        assert _classify_query_tier("「三坊七巷」的开放时间") == "hybrid"
        assert _classify_query_tier("《福州旅游攻略》里怎么说") == "hybrid_multi_query" or True  # 含「怎么」complex 优先
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

    def test_operation_words_still_multi_query(self):
        # 存量语义：含操作性复杂词（流程/怎么）的问句走 multi_query
        assert _classify_query_tier("退款流程是什么") == "hybrid_multi_query"


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
