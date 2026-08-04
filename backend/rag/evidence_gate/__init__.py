"""evidence_gate 包 — PR-1.1 起扩展为子包，PR-2.x 拆分为多模块。

Evidence Gate — RAG 主动拒答决策模块

设计对标:
  - RAGFlow (cosine ≥ 0.2 单阈值)
  - LangGraph CRAG (retrieval grader + self-correction)
  - Vertex AI / AWS Bedrock (groundedness + citation match)
  - RAGAS faithfulness

模块结构（PR-2.x 拆分后）:
  - models.py:     领域模型（RejectReason, GateDecision, RejectInfo）
  - operations.py: 门控逻辑（retrieval/rerank/evaluation gate + helpers）
  - controller.py: EvidenceGateController（PR-1.1）
  - self_correction.py: SelfCorrectionStrategy（PR-1.2）

接入点:
  - hybrid.py: hybrid_retrieve() 出口 → evidence_gate_retrieval()
  - chain.py: chain.invoke 后 Rerank Gate → evidence_gate_rerank()
  - chain.py: _evaluate() 末尾 → is_groundedness_acceptable()

文档: docs/architecture/rag-evidence-gate.md
"""
from backend.rag.evidence_gate.models import (  # noqa: F401
    RejectReason,
    REJECT_MESSAGES,
    GateDecision,
    RejectInfo,
    HIGH_RISK_DOC_TYPES,
    INTENT_RISK_LEVEL,
)
from backend.rag.evidence_gate.operations import (  # noqa: F401
    risk_level_from_intent_and_doctype,
    evidence_gate_retrieval,
    evidence_gate_rerank,
    is_groundedness_acceptable,
    parse_meta_comment,
    build_rejection_response,
    is_evidence_gate_enabled,
    gate_retrieval_passthrough,
)
from backend.rag.evidence_gate.controller import EvidenceGateController  # noqa: F401
from backend.rag.evidence_gate.self_correction import SelfCorrectionStrategy  # noqa: F401

__all__ = [
    # models
    "RejectReason",
    "REJECT_MESSAGES",
    "GateDecision",
    "RejectInfo",
    "HIGH_RISK_DOC_TYPES",
    "INTENT_RISK_LEVEL",
    # operations
    "risk_level_from_intent_and_doctype",
    "evidence_gate_retrieval",
    "evidence_gate_rerank",
    "is_groundedness_acceptable",
    "parse_meta_comment",
    "build_rejection_response",
    "is_evidence_gate_enabled",
    "gate_retrieval_passthrough",
    # strategy objects
    "EvidenceGateController",
    "SelfCorrectionStrategy",
]
