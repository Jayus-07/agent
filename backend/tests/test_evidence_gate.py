"""Test Evidence Gate — P0 改造单测

覆盖:
  1. risk_level_from_intent_and_doctype() 各分支
  2. evidence_gate_retrieval() 5 种决策路径
  3. evidence_gate_rerank() top1/avg/gap 三维判定
  4. parse_meta_comment() 提取 Markdown + META 注释
  5. build_rejection_response() 拒答信息完整性
  6. RejectReason 枚举覆盖企业实践 5 类
  7. config/rag.py Evidence Gate 默认值与企业实践对齐
"""
import pytest
from langchain_core.documents import Document

from backend.rag.evidence_gate import (
    RejectReason,
    GateDecision,
    RejectInfo,
    REJECT_MESSAGES,
    risk_level_from_intent_and_doctype,
    evidence_gate_retrieval,
    evidence_gate_rerank,
    is_groundedness_acceptable,
    parse_meta_comment,
    build_rejection_response,
    is_evidence_gate_enabled,
    HIGH_RISK_DOC_TYPES,
)
from backend.config import (
    EVIDENCE_GATE_ENABLED,
    VEC_MIN_SCORE,
    DOC_TYPE_COVERAGE_REQUIRED,
    RERANK_MIN_TOP1,
    RERANK_MIN_AVG,
    RERANK_HIGH_RISK_MIN_TOP1,
    FAITHFULNESS_REJECT_SCORE,
    ENABLE_FAITHFULNESS,
    SELF_CORRECTION_ENABLED,
)


# =====================================================
# 1. risk_level_from_intent_and_doctype
# =====================================================

class TestRiskLevel:
    """§C1 修复：去虚构 intent，改用 doc_type 推导"""

    def test_policy_doc_type_upgrades_to_high(self):
        """命中 policy/compliance/legal 即便 summary 也升 high"""
        assert risk_level_from_intent_and_doctype("summary_query", ["policy"]) == "high"
        assert risk_level_from_intent_and_doctype("entity_query", ["compliance"]) == "high"
        assert risk_level_from_intent_and_doctype("summary_query", ["legal"]) == "high"

    def test_fact_query_with_doc_types_means_finance_hr(self):
        """fact_query + 任意 doc_types 视为高风险（财务/人事事实）"""
        assert risk_level_from_intent_and_doctype("fact_query", ["sop"]) == "high"

    def test_normal_queries_stay_low(self):
        assert risk_level_from_intent_and_doctype("entity_query", ["general"]) == "low"
        assert risk_level_from_intent_and_doctype("summary_query", ["general"]) == "low"

    def test_unknown_intent_falls_back_to_low(self):
        assert risk_level_from_intent_and_doctype("nonexistent_intent", []) == "low"

    def test_empty_doc_types_with_fact_query(self):
        """fact_query 但 doc_types 空 → INTENT_RISK_LEVEL 默认值 medium"""
        assert risk_level_from_intent_and_doctype("fact_query", []) == "medium"


# =====================================================
# 2. evidence_gate_retrieval
# =====================================================

def _doc(chunk_id: str, score: float = 0.5, doc_type: str = "general",
          source: str = "test.md") -> Document:
    return Document(
        page_content=f"content-{chunk_id}",
        metadata={"chunk_id": chunk_id, "rerank_score": score,
                  "rrf_score": score, "doc_type": doc_type,
                  "source_file": source},
    )


class TestEvidenceGateRetrieval:
    def test_empty_docs_returns_no_evidence(self):
        d = evidence_gate_retrieval([], vec_min_score=0.2, require_doc_type_coverage=False)
        assert not d.passed
        assert d.reason == RejectReason.NO_EVIDENCE
        assert d.diagnostics["doc_count"] == 0

    def test_low_score_returns_low_relevance(self):
        docs = [_doc("c1", score=0.05), _doc("c2", score=0.03)]
        d = evidence_gate_retrieval(docs, vec_min_score=0.2, require_doc_type_coverage=False)
        assert not d.passed
        assert d.reason == RejectReason.LOW_RELEVANCE
        assert d.diagnostics["top_score"] < 0.2

    def test_passes_when_score_above_threshold(self):
        docs = [_doc("c1", score=0.5), _doc("c2", score=0.4)]
        d = evidence_gate_retrieval(docs, vec_min_score=0.2, require_doc_type_coverage=False)
        assert d.passed
        assert d.reason is None
        assert d.score == 0.5

    def test_doc_type_coverage_required_but_missing(self):
        """QueryAnalyzer 推导需要 policy，实际召回 general → DOC_TYPE_MISMATCH"""
        docs = [_doc("c1", score=0.5, doc_type="general")]
        qa_mock = type("QA", (), {"doc_types": ["policy"]})()
        d = evidence_gate_retrieval(docs, query_analysis=qa_mock,
                                     vec_min_score=0.2, require_doc_type_coverage=True)
        assert not d.passed
        assert d.reason == RejectReason.DOC_TYPE_MISMATCH
        assert "policy" in d.diagnostics["expected_types"]

    def test_doc_type_coverage_required_and_match(self):
        docs = [_doc("c1", score=0.5, doc_type="policy")]
        qa_mock = type("QA", (), {"doc_types": ["policy"]})()
        d = evidence_gate_retrieval(docs, query_analysis=qa_mock,
                                     vec_min_score=0.2, require_doc_type_coverage=True)
        assert d.passed

    def test_doc_type_coverage_disabled_bypasses_check(self):
        """require_doc_type_coverage=False 时即使 doc_types 不匹配也通过"""
        docs = [_doc("c1", score=0.5, doc_type="general")]
        qa_mock = type("QA", (), {"doc_types": ["policy"]})()
        d = evidence_gate_retrieval(docs, query_analysis=qa_mock,
                                     vec_min_score=0.2, require_doc_type_coverage=False)
        assert d.passed


# =====================================================
# 3. evidence_gate_rerank
# =====================================================

class TestEvidenceGateRerank:
    def test_empty_docs_returns_no_evidence(self):
        d = evidence_gate_rerank([])
        assert not d.passed
        assert d.reason == RejectReason.NO_EVIDENCE

    def test_top1_below_threshold_fails(self):
        docs = [_doc("c1", score=0.2)]  # 默认 0.35
        d = evidence_gate_rerank(docs, min_top1=0.35, min_avg=0.25, min_gap=0.05)
        assert not d.passed
        assert d.reason == RejectReason.INSUFFICIENT
        assert d.diagnostics["failed_rule"] == "top1"

    def test_top1_passes_but_avg_fails(self):
        docs = [_doc("c1", score=0.5), _doc("c2", score=0.1)]
        # avg = 0.3 ≥ 0.25 通过？实际 0.3 ≥ 0.25 通过；改 test
        # 让 top1=0.5 pass, doc2=0.01 → avg=0.255 pass; 改两值
        docs = [_doc("c1", score=0.5), _doc("c2", score=0.05)]
        d = evidence_gate_rerank(docs, min_top1=0.35, min_avg=0.30, min_gap=0.05)
        # top1=0.5 pass; avg=0.275 < 0.30 fail
        assert not d.passed
        assert d.diagnostics["failed_rule"] == "avg"

    def test_high_risk_uses_higher_top1_threshold(self):
        """high_risk 用 high_risk_min_top1=0.55，普通 0.35"""
        docs = [_doc("c1", score=0.45)]
        d_low = evidence_gate_rerank(docs, risk_level="low",
                                      min_top1=0.35, high_risk_min_top1=0.55)
        d_high = evidence_gate_rerank(docs, risk_level="high",
                                       min_top1=0.35, high_risk_min_top1=0.55)
        assert d_low.passed   # 0.45 ≥ 0.35
        assert not d_high.passed  # 0.45 < 0.55

    def test_all_pass(self):
        docs = [_doc("c1", score=0.8), _doc("c2", score=0.7), _doc("c3", score=0.5)]
        d = evidence_gate_rerank(docs, min_top1=0.35, min_avg=0.25, min_gap=0.05)
        assert d.passed
        assert d.score == 0.8
        assert d.diagnostics["doc_count"] == 3


# =====================================================
# 4. parse_meta_comment
# =====================================================

class TestParseMetaComment:
    def test_no_meta_returns_empty(self):
        cleaned, meta = parse_meta_comment("纯文本回答")
        assert "纯文本" in cleaned
        assert meta == {}

    def test_extracts_meta(self):
        raw = '这是答案正文。<!--META{"can_answer":true,"citations":[1,2]}-->'
        cleaned, meta = parse_meta_comment(raw)
        assert "答案正文" in cleaned
        assert "META" not in cleaned
        assert meta == {"can_answer": True, "citations": [1, 2]}

    def test_invalid_json_keeps_text(self):
        raw = '正文<!--META{invalid json}-->'
        cleaned, meta = parse_meta_comment(raw)
        assert meta == {}
        assert "正文" in cleaned

    def test_non_dict_meta_skipped(self):
        raw = '正文<!--META["array", "not dict"]-->'
        cleaned, meta = parse_meta_comment(raw)
        assert meta == {}


# =====================================================
# 5. build_rejection_response
# =====================================================

class TestBuildRejectionResponse:
    def test_no_evidence_response(self):
        decision = GateDecision(passed=False, reason=RejectReason.NO_EVIDENCE,
                                layer="retrieval",
                                diagnostics={"doc_count": 0, "threshold": {"vec_min": 0.2}})
        msg, info = build_rejection_response(decision, "retrieval")
        assert "知识库暂无" in msg
        assert info.rejected is True
        assert info.reason == "no_evidence"
        assert info.layer == "retrieval"
        assert "timestamp" in info.to_dict()

    def test_low_relevance_response(self):
        decision = GateDecision(passed=False, reason=RejectReason.LOW_RELEVANCE,
                                layer="retrieval",
                                score=0.05,
                                diagnostics={"top_score": 0.05, "doc_count": 3})
        msg, info = build_rejection_response(decision, "retrieval")
        assert "相关性不足" in msg
        assert info.scores["top_score"] == 0.05

    def test_self_correction_attempted_appended(self):
        decision = GateDecision(passed=False, reason=RejectReason.INSUFFICIENT,
                                layer="rerank",
                                diagnostics={"doc_count": 5})
        msg, info = build_rejection_response(decision, "rerank",
                                              self_correction_attempted=True)
        assert "改写提问" in msg
        assert info.self_correction_attempted is True

    def test_unknown_reason_fallback_message(self):
        # 手工构造（绕过枚举防止未来 enum 改）
        # 这里用 INSUFFICIENT 测一个不存在的自定义 reason 不会炸
        decision = GateDecision(passed=False, reason=None,
                                layer="retrieval")
        msg, info = build_rejection_response(decision, "retrieval")
        # 无 reason 时 msg 为空
        assert info.reason is None


# =====================================================
# 6. is_groundedness_acceptable
# =====================================================

class TestGroundednessAcceptable:
    def test_low_score_for_normal_rejects(self):
        ok, reason = is_groundedness_acceptable(0.4, risk_level="low",
                                                  low_threshold=0.5, high_threshold=0.7)
        assert not ok
        assert reason == RejectReason.HALLUCINATION

    def test_high_risk_uses_higher_threshold(self):
        ok, _ = is_groundedness_acceptable(0.6, risk_level="high",
                                            low_threshold=0.5, high_threshold=0.7)
        assert not ok  # 0.6 < 0.7
        ok, _ = is_groundedness_acceptable(0.6, risk_level="low",
                                            low_threshold=0.5, high_threshold=0.7)
        assert ok     # 0.6 ≥ 0.5


# =====================================================
# 7. RejectReason 枚举（企业实践 5 类）
# =====================================================

class TestRejectReasonEnum:
    def test_exactly_5_reasons(self):
        """§0.3 精简到 5 类：no_evidence / low_relevance / doc_type_mismatch / insufficient / hallucination"""
        assert len(RejectReason) == 5
        values = {r.value for r in RejectReason}
        assert values == {
            "no_evidence", "low_relevance", "doc_type_mismatch",
            "insufficient", "hallucination",
        }

    def test_all_reasons_have_user_facing_message(self):
        for r in RejectReason:
            assert r in REJECT_MESSAGES, f"{r} 缺少 user-facing message"
            assert len(REJECT_MESSAGES[r]) > 0


# =====================================================
# 8. config/rag.py 默认值对齐企业实践
# ====================================

class TestConfigDefaults:
    """§0 章节对标：默认值必须与 RAGFlow / Vertex / AWS 一致或更严"""

    def test_faithfulness_default_true(self):
        """§0.2 行业默认开"""
        assert ENABLE_FAITHFULNESS is True

    def test_vec_min_score_aligns_with_ragflow(self):
        """RAGFlow 默认 0.2（COSINE 阈值）"""
        assert VEC_MIN_SCORE == 0.2

    def test_doc_type_coverage_default_true(self):
        """企业主流开"""
        assert DOC_TYPE_COVERAGE_REQUIRED is True

    def test_rerank_top1_threshold_reasonable(self):
        """与现有 RERANK_SCORE_THRESHOLD=0.3 相比应有上限控制（不放过低）"""
        assert RERANK_MIN_TOP1 >= 0.3
        assert RERANK_MIN_TOP1 <= 0.5

    def test_high_risk_stricter_than_low(self):
        assert RERANK_HIGH_RISK_MIN_TOP1 > RERANK_MIN_TOP1

    def test_faithfulness_reject_threshold(self):
        assert FAITHFULNESS_REJECT_SCORE == 0.5

    def test_self_correction_default_on(self):
        """借鉴 CRAG self-correction (LLM rewrite 重试)"""
        assert SELF_CORRECTION_ENABLED is True

    def test_evidence_gate_total_switch(self):
        assert EVIDENCE_GATE_ENABLED is True
        assert is_evidence_gate_enabled() is True


# =====================================================
# 9. 配置不存在/被禁用时的回退
# =====================================================

class TestBypass:
    """EVIDENCE_GATE_ENABLED=false 时不应拒答（旁路）"""

    def test_passthrough_decision_always_passes(self):
        from backend.rag.evidence_gate import gate_retrieval_passthrough
        d = gate_retrieval_passthrough()
        assert d.passed is True
        assert d.diagnostics.get("gate_bypassed") is True
