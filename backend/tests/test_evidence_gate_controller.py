"""PR-1.1 — EvidenceGateController 单测。

覆盖：
- 初始 state 正确（默认 intent / risk_level）
- set_intent / set_risk_level / set_query_analysis 工作
- build_decision_from_meta 正确处理 5 种 RejectReason + 兜底 NO_EVIDENCE
- 状态隔离（多个 controller 实例不互相影响）
- 与原 RAGChain._build_decision_from_meta 行为兼容
"""
import pytest

from backend.rag.evidence_gate import (
    EvidenceGateController,
    GateDecision,
    RejectReason,
)


class TestInitialState:
    def test_default_intent(self):
        c = EvidenceGateController()
        assert c.intent == "summary_query"

    def test_default_risk_level(self):
        c = EvidenceGateController()
        assert c.risk_level == "low"

    def test_default_query_analysis(self):
        c = EvidenceGateController()
        assert c.query_analysis is None

    def test_is_high_risk_false(self):
        c = EvidenceGateController()
        assert c.is_high_risk() is False


class TestStateWrites:
    def test_set_intent(self):
        c = EvidenceGateController()
        c.set_intent("compliance_query")
        assert c.intent == "compliance_query"

    def test_set_risk_level(self):
        c = EvidenceGateController()
        c.set_risk_level("high")
        assert c.risk_level == "high"
        assert c.is_high_risk() is True

    def test_set_query_analysis_extracts_intent(self):
        c = EvidenceGateController()

        class _QA:
            intent = "fact_query"
            doc_types = ["policy"]

        c.set_query_analysis(_QA())
        assert c.intent == "fact_query"
        assert c.query_analysis is not None
        assert c.query_analysis.intent == "fact_query"

    def test_set_query_analysis_none_keeps_intent(self):
        c = EvidenceGateController()
        c.set_intent("order_query")
        c.set_query_analysis(None)  # 不应清掉 intent
        assert c.intent == "order_query"

    def test_set_query_analysis_without_intent_keeps(self):
        c = EvidenceGateController()
        c.set_intent("order_query")

        class _QA:
            doc_types = ["x"]
            # 无 intent 字段

        c.set_query_analysis(_QA())
        assert c.intent == "order_query"  # 保留


class TestBuildDecisionFromMeta:
    def test_no_evidence_default(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({})
        assert d.passed is False
        assert d.reason == RejectReason.NO_EVIDENCE
        assert d.layer == "generation"
        assert d.score == 0.0

    def test_hallucination_reason(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "hallucination", "confidence": 0.8})
        assert d.reason == RejectReason.HALLUCINATION
        assert d.score == 0.8

    def test_low_relevance_reason(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "low_relevance", "confidence": 0.4})
        assert d.reason == RejectReason.LOW_RELEVANCE

    def test_unknown_reason_falls_back_to_no_evidence(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "nonsense_value"})
        assert d.reason == RejectReason.NO_EVIDENCE

    def test_case_insensitive_reason(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "HALLUCINATION"})
        assert d.reason == RejectReason.HALLUCINATION

    def test_diagnostics_carry_citations(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "no_evidence", "citations": ["doc1", "doc2"]})
        assert d.diagnostics["meta_citations"] == ["doc1", "doc2"]
        assert d.diagnostics["meta_confidence"] == 0.0
        assert "meta_min_confidence" in d.diagnostics["threshold"]


class TestIsolation:
    def test_two_instances_independent(self):
        c1 = EvidenceGateController()
        c2 = EvidenceGateController()
        c1.set_intent("a")
        c2.set_intent("b")
        assert c1.intent == "a"
        assert c2.intent == "b"


class TestCompatWithRAGChain:
    """与原 RAGChain._build_decision_from_meta 行为等价。"""

    def test_layer_is_generation(self):
        c = EvidenceGateController()
        d = c.build_decision_from_meta({"reason": "no_evidence"})
        assert d.layer == "generation"

    def test_passed_always_false(self):
        """meta 触发的决策一定 passed=False（拒答方向）。"""
        c = EvidenceGateController()
        for reason in ["no_evidence", "low_relevance", "hallucination"]:
            d = c.build_decision_from_meta({"reason": reason, "confidence": 0.5})
            assert d.passed is False
