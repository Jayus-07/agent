"""test_indexing_rules.py — 阶段1 规则收口（config/indexing_rules.py）。

契约：
  1. 默认值下 assess_quality / classify_with_confidence / detect_business_domain
     行为与收口前完全一致（回归保护）；
  2. override_rules() 运行时覆盖立即生效，reset_rules_override() 还原；
  3. 环境变量覆盖生效。
"""
import importlib

import pytest


@pytest.fixture(autouse=True)
def _clean_overrides():
    from backend.config.indexing_rules import reset_rules_override
    reset_rules_override()
    yield
    reset_rules_override()


# ============ 默认值回归 ============

class TestDefaultsUnchanged:
    """默认规则下，关键阈值与收口前硬编码一致。"""

    def test_default_rules_values(self):
        from backend.config.indexing_rules import get_rules
        r = get_rules()
        assert r.near_dup_similarity_threshold == 0.85
        assert r.minhash_n_hashes == 128
        assert r.minhash_n_gram == 3
        assert r.quality_reject_below == 40
        assert r.quality_warn_below == 60
        assert r.classify_filename_weight == 100
        assert r.classify_title_weight == 20
        assert r.classify_folder_weight == 40
        assert r.classify_folder_conf_bonus == 0.3
        assert r.arbitration_score_gap == 5
        assert r.domain_min_score == 2
        assert r.simhash_hamming_threshold == 3

    def test_quality_gate_default_scores(self):
        """默认权重下质量门禁四维满分仍为 40/30/20/10。"""
        from backend.rag.preprocessing.metadata import assess_quality
        good_doc = "# 标题\n" + ("第一章 总则\n" * 3) + ("这是一段足够长的正式内容文本。" * 100)
        q = assess_quality(good_doc)
        assert q["dimensions"]["completeness"] == 40
        assert q["dimensions"]["structure"] == 30
        assert q["dimensions"]["noise"] == 20
        assert q["passed"] is True

    def test_classify_contract_preserved(self):
        """legal 条款折半行为与 return_detail 结构不变。"""
        from backend.rag.preprocessing.metadata import classify_with_confidence
        base = "甲乙双方签订合同，约定条款、违约责任与赔偿。"
        with_clause = base + "\n第一条 双方义务\n第二条 违约责任\n第三条 赔偿范围"
        _, _, d_no = classify_with_confidence(base, return_detail=True)
        _, _, d_yes = classify_with_confidence(with_clause, return_detail=True)
        assert d_yes["scores"].get("legal", 0) > d_no["scores"].get("legal", 0)
        assert set(d_no) >= {"scores", "filename_hits", "title_hits", "folder_hit",
                             "llm_fallback", "keyword_hits"}

    def test_minhash_defaults_match(self):
        from backend.config.indexing_rules import get_rules
        from backend.rag.preprocessing.metadata import compute_minhash
        sig = compute_minhash("这是一段用于计算 MinHash 签名的测试文本。" * 20)
        assert len(sig) == get_rules().minhash_n_hashes == 128

    def test_minhash_signature_uses_config_chars(self):
        """签名参与文本长度取自配置（默认 5000 字截断）。"""
        from backend.config.indexing_rules import get_rules
        from backend.rag.preprocessing.metadata import compute_minhash
        text = "字" * 8000
        sig_full = compute_minhash(text)
        sig_5000 = compute_minhash(text[:get_rules().minhash_text_max_chars])
        assert sig_full == sig_5000


# ============ 运行时覆盖 ============

class TestOverride:
    def test_override_near_dup_threshold(self):
        from backend.config.indexing_rules import get_rules, override_rules
        override_rules(near_dup_similarity_threshold=0.5)
        assert get_rules().near_dup_similarity_threshold == 0.5

    def test_override_affects_classify_weight(self):
        """文件名权重改为 10 后，命中得分相应变化。"""
        from backend.config.indexing_rules import override_rules
        from backend.rag.preprocessing.metadata import classify_with_confidence
        text = "采购申请需审批，操作流程如下。"
        _, _, d_default = classify_with_confidence(text, filename="采购流程.docx",
                                                   return_detail=True)
        override_rules(classify_filename_weight=10)
        _, _, d_small = classify_with_confidence(text, filename="采购流程.docx",
                                                 return_detail=True)
        assert d_small["scores"].get("sop", 0) < d_default["scores"].get("sop", 0)

    def test_override_ignores_unknown_fields(self):
        from backend.config.indexing_rules import get_rules, override_rules
        override_rules(nonexistent_field=1, near_dup_similarity_threshold=None)
        # 未传有效字段 → 规则不变，也不抛异常
        assert get_rules().near_dup_similarity_threshold == 0.85

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DOMAIN_MIN_SCORE", "7")
        import backend.config.indexing_rules as ir
        importlib.reload(ir)
        try:
            assert ir.get_rules().domain_min_score == 7
        finally:
            # reload 会重置缓存，无需额外清理
            pass
