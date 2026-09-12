"""test_domain_detector.py — DomainDetector 双通道检测测试"""
import re
from unittest.mock import MagicMock

from backend.customer_service.router.domain_detector import DomainDetector


def _make_detector_with_mocks(
    rule_hits: list[str] | None = None,
    vector_score: float = 0.0,
):
    """构造一个跳过 Chroma 初始化的 DomainDetector，直接注入通道结果。"""
    det = DomainDetector.__new__(DomainDetector)
    det._collection = None
    det._rule_channel = MagicMock(
        return_value=(rule_hits or [], min(len(rule_hits or []) / 3.0, 1.0))
    )
    det._vector_channel = MagicMock(return_value=vector_score)
    return det


class TestRuleChannel:
    def test_hit_multiple_domains(self):
        det = DomainDetector.__new__(DomainDetector)
        det._collection = None
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
        det = DomainDetector.__new__(DomainDetector)
        det._collection = None
        patterns = {
            "KNOWLEDGE": [re.compile(r"怎么办")],
            "AFTER_SALES": [re.compile(r"退款")],
        }
        hits, score = det._rule_channel("今天天气不错", patterns)
        assert hits == []
        assert score == 0.0

    def test_score_capped_at_one(self):
        det = DomainDetector.__new__(DomainDetector)
        det._collection = None
        patterns = {
            f"D{i}": [re.compile(r"test")] for i in range(10)
        }
        hits, score = det._rule_channel("test", patterns)
        assert score <= 1.0


class TestVectorChannel:
    def test_returns_zero_when_no_collection(self):
        det = DomainDetector.__new__(DomainDetector)
        det._collection = None
        assert det._vector_channel("任何查询") == 0.0

    def test_returns_score_from_similarity(self):
        det = DomainDetector.__new__(DomainDetector)
        mock_doc = MagicMock()
        mock_doc.metadata = {"domain": "KNOWLEDGE"}
        mock_collection = MagicMock()
        mock_collection._collection.count.return_value = 5
        mock_collection.similarity_search_with_score.return_value = [
            (mock_doc, 0.5)
        ]
        det._collection = mock_collection
        score = det._vector_channel("怎么办")
        assert abs(score - 1.0 / 1.5) < 0.01

    def test_returns_zero_on_exception(self):
        det = DomainDetector.__new__(DomainDetector)
        mock_collection = MagicMock()
        mock_collection._collection.count.return_value = 1
        mock_collection.similarity_search_with_score.side_effect = RuntimeError("fail")
        det._collection = mock_collection
        assert det._vector_channel("test") == 0.0


class TestDualChannelLogic:
    def test_both_pass_is_cs_true(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        det = _make_detector_with_mocks(rule_hits=["KNOWLEDGE", "AFTER_SALES"], vector_score=0.80)
        result = det.detect("怎么办退款")
        assert result.is_cs is True
        assert len(result.rule_hits) == 2

    def test_rule_pass_vector_fail_is_cs_false(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        det = _make_detector_with_mocks(rule_hits=["KNOWLEDGE", "AFTER_SALES"], vector_score=0.30)
        result = det.detect("怎么办退款")
        assert result.is_cs is False

    def test_rule_fail_vector_decide_is_cs_true(self, monkeypatch):
        """向量强匹配（≥CS_VECTOR_DECIDE）单独决定，无需规则双命中。"""
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_DECIDE", 0.85
        )
        det = _make_detector_with_mocks(rule_hits=["KNOWLEDGE"], vector_score=0.90)
        result = det.detect("怎么办")
        assert result.is_cs is True
        assert "vec_decide=" in result.reason

    def test_rule_fail_vector_mid_band_is_cs_false(self, monkeypatch):
        """向量在 [阈值, 决定线) 区间且规则不足 → 仍不判定（保守）。"""
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_DECIDE", 0.85
        )
        det = _make_detector_with_mocks(rule_hits=["KNOWLEDGE"], vector_score=0.75)
        result = det.detect("怎么办")
        assert result.is_cs is False

    def test_both_fail_is_cs_false(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        det = _make_detector_with_mocks(rule_hits=[], vector_score=0.10)
        result = det.detect("今天天气不错")
        assert result.is_cs is False
        assert result.reason == "no_match"

    def test_reason_includes_passing_channels(self, monkeypatch):
        monkeypatch.setattr(
            "backend.config.customer_service.CS_RULE_MIN_HITS", 2
        )
        monkeypatch.setattr(
            "backend.config.customer_service.CS_VECTOR_THRESHOLD", 0.70
        )
        det = _make_detector_with_mocks(rule_hits=["KNOWLEDGE", "AFTER_SALES"], vector_score=0.85)
        result = det.detect("怎么办退款")
        assert "rule=" in result.reason
        # 0.85 命中强决定线，reason 走 vec_decide 分支
        assert "vec_decide=" in result.reason
