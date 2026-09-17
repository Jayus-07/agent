"""test_domain_detector.py — DomainDetector 纯规则检测测试

（2026-09-17 删除 Chroma 向量通道，全站存储收口 pgvector：
 is_cs = 规则命中数 ≥ CS_RULE_MIN_HITS，vector_score 恒 0.0）
"""
import re

from backend.customer_service.router.domain_detector import DomainDetector


class TestRuleChannel:
    def test_hit_multiple_domains(self):
        det = DomainDetector()
        patterns = {
            "KNOWLEDGE": [re.compile(r"怎么办")],
            "AFTER_SALES": [re.compile(r"退款")],
            "TRANSACTION": [re.compile(r"订单")],
        }
        hits, score = det._rule_channel("怎么办退款", patterns)
        assert len(hits) == 2
        assert "KNOWLEDGE" in hits
        assert "AFTER_SALES" in hits

    def test_miss_no_patterns(self):
        det = DomainDetector()
        patterns = {
            "KNOWLEDGE": [re.compile(r"怎么办")],
            "AFTER_SALES": [re.compile(r"退款")],
        }
        hits, score = det._rule_channel("今天天气不错", patterns)
        assert hits == []
        assert score == 0.0

    def test_score_capped_at_one(self):
        det = DomainDetector()
        patterns = {
            f"D{i}": [re.compile(r"test")] for i in range(10)
        }
        hits, score = det._rule_channel("test", patterns)
        assert score <= 1.0


class TestRuleOnlyDetection:
    """is_cs = 规则命中数 ≥ CS_RULE_MIN_HITS（默认 2）。"""

    def test_enough_hits_is_cs_true(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        det = DomainDetector()
        result = det.detect("怎么办退款")
        assert result.is_cs is True
        assert len(result.rule_hits) == 2
        assert result.vector_score == 0.0
        assert "rule=" in result.reason

    def test_insufficient_hits_is_cs_false(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        det = DomainDetector()
        result = det.detect("怎么办")
        assert result.is_cs is False

    def test_no_hits_is_cs_false(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        det = DomainDetector()
        result = det.detect("今天天气不错")
        assert result.is_cs is False
        assert result.reason == "no_match"

    def test_min_hits_lowered_to_one(self, monkeypatch):
        """CS_RULE_MIN_HITS=1 时单域命中即判定（进线率调参入口）。"""
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 1
        )
        det = DomainDetector()
        result = det.detect("怎么办")
        assert result.is_cs is True

    def test_vector_score_field_kept_zero(self):
        """vector_score 字段保留恒 0.0（CSDetection 消费方兼容）。"""
        det = DomainDetector()
        result = det.detect("退款退货怎么办")
        assert result.vector_score == 0.0
