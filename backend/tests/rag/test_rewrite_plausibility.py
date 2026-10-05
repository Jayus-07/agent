"""tests/rag/test_rewrite_plausibility.py — MultiQuery 变体合法性过滤（D-11）

实测缺陷：doubao 思考型模型偶发把检索内容/上下文原文当作改写查询输出
（数百字陈述句）→ chunk 原文再检索 → 自匹配 → EvidenceGate 拒答。
过滤规则：变体长度 ≤ max(原问题×2, 40 字) 且 ≥4 字；全部不合法回退原 query。
"""
from __future__ import annotations

from backend.rag.retrieval.multi_query import _plausible_variant


class TestPlausibleVariant:
    def test_chunk_original_text_rejected(self):
        """chunk 原文（数百字陈述句）必须被拒——D-11 主场景。"""
        chunk = ("乌塔（定光多宝塔）建于唐代贞元年间（公元791年），由闽王王审知主持建造，"
                 "是福州现存最古老的石塔之一，塔身用花岗岩砌筑，高35米，平面八角形，共七层。") * 3
        assert _plausible_variant(chunk, "乌塔建于什么年代") is False

    def test_normal_variants_pass(self):
        q = "乌塔建于什么年代"
        assert _plausible_variant("乌塔的建造年代", q) is True
        assert _plausible_variant("乌塔 历史 始建年份", q) is True

    def test_length_cap_relative_to_question(self):
        """上限 = max(原问题×2, 40)：短问允许到 40 字，长问按 2 倍收紧。"""
        short_q = "乌塔多高"
        assert _plausible_variant("a" * 40, short_q) is True
        assert _plausible_variant("a" * 41, short_q) is False
        long_q = "这是一条特别长的原始问题需要收紧上限" * 3
        assert _plausible_variant("a" * (len(long_q) * 2 + 1), long_q) is False

    def test_too_short_rejected(self):
        assert _plausible_variant("abc", "任意问题") is False
        assert _plausible_variant("", "任意问题") is False
