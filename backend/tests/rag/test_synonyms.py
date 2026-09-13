"""口语化同义词扩展测试（2026-09-14 回归）。

背景："发欧洲大概要多少天"类口语 query 与书面时效文档（"配送时效"
"工作日"）词面零重叠，Stage 1/2 均难以命中。本次在 SYNONYMS 中补齐
"多少天/几天/多久 ↔ 时效"双向映射 + "发货"口语变体，供
ChunkLevelRetriever 检索扩展与 EvidenceGate 实体闭包共用。
"""
from backend.rag.preprocessing.synonyms import SYNONYMS, expand_query


class TestColloquialExpansion:
    def test_shipping_time_colloquial_query_expands(self):
        """口语时效问法 → 生成 几天/多久/时效 变体（含原始 query）。"""
        variants = expand_query("发欧洲大概要多少天")
        assert variants[0] == "发欧洲大概要多少天"
        joined = " ".join(variants)
        assert "几天" in joined
        assert "多久" in joined
        assert "时效" in joined

    def test_variant_keys_bidirectional(self):
        """多少天/几天/多久/时效 四个入口任意一个都能扩出同组变体。"""
        for key, other in (("多少天", "几天"), ("几天", "多久"),
                           ("多久", "时效"), ("时效", "多少天")):
            variants = expand_query(f"跨境包裹{key}")
            assert any(other in v for v in variants), f"{key} 未扩出 {other}"

    def test_ship_colloquial_variants(self):
        """"发货"口语变体（寄出/寄送/发走）可用于 BM25 词面补齐。"""
        variants = expand_query("什么时候发货")
        assert len(variants) > 1
        assert any("寄出" in v or "寄送" in v for v in variants)

    def test_no_hit_query_returns_original_only(self):
        """无词表命中的 query → 仅返回原始 query（不产生空转变体）。"""
        variants = expand_query("zzz 完全无关词")
        assert variants == ["zzz 完全无关词"]

    def test_max_expansions_cap(self):
        """变体数不超过 max_expansions + 1（控制 RRF 融合成本）。"""
        variants = expand_query("发货多少天几天多久时效")
        assert len(variants) <= 5

    def test_group_consistency(self):
        """时效组四个 key 互为组内词（EvidenceGate 实体闭包依赖此性质）。"""
        group = {"多少天", "几天", "多久", "时效"}
        for key in group:
            assert set(SYNONYMS[key]) >= group - {key}, f"{key} 组内缺词"
