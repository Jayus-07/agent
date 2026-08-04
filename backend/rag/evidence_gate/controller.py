"""Evidence Gate Controller — PR-1.1。

RAGChain god class 拆分第一步：把 5 个 RejectReason 决策相关状态 + 决策构造
从 RAGChain 抽出，封装到独立对象。

设计动机（ADR-0002）：
- RAGChain 当前持有 6 个 mutable 字段（_last_intent / _risk_level / _last_query_analysis
  / _last_query / _last_meta / _self_correction_*），跨方法读写，难以单测
- 决策构造（_build_decision_from_meta）只有 11 行，但耦合在 886 行 god class 中
- 抽出后 RAGChain 变成 orchestrator，EvidenceGateController 自治状态 + 决策

边界（PR-1.1 范围）：
- ✅ 抽出 _last_intent, _risk_level, _last_query_analysis
- ✅ 抽出 _build_decision_from_meta
- ❌ 不动 _last_query / _last_meta（PR-1.2 SelfCorrection 用）
- ❌ 不动 _self_correction_*（PR-1.2）
- ❌ 不动 _run_evidence_gates（保留在 RAGChain 中，PR-1.4 再下移）
"""
from __future__ import annotations

from typing import Any


class EvidenceGateController:
    """Evidence Gate 决策状态 + 决策构造。

    状态：
      - intent: 上次 query 解析的 intent
      - risk_level: 风险等级 low | medium | high
      - last_query_analysis: QueryAnalyzer 输出

    用法：
        controller = EvidenceGateController()
        controller.set_intent("compliance_query")
        controller.set_risk_level("high")
        decision = controller.build_decision_from_meta({"reason": "no_evidence", "confidence": 0.3})
    """

    def __init__(self):
        self._last_intent: str = "summary_query"
        self._risk_level: str = "low"
        self._last_query_analysis: Any = None

    # =====================================================
    # 状态写入 / 读取
    # =====================================================

    def set_intent(self, intent: str) -> None:
        self._last_intent = intent

    def set_risk_level(self, level: str) -> None:
        self._risk_level = level

    def set_query_analysis(self, analysis: Any) -> None:
        """设置上次 query 解析结果（含 intent / doc_types 等）。"""
        self._last_query_analysis = analysis
        if analysis is not None and getattr(analysis, "intent", None):
            self._last_intent = analysis.intent

    @property
    def intent(self) -> str:
        return self._last_intent

    @property
    def risk_level(self) -> str:
        return self._risk_level

    @property
    def query_analysis(self) -> Any:
        return self._last_query_analysis

    def is_high_risk(self) -> bool:
        return self._risk_level == "high"

    # =====================================================
    # 决策构造（从 LLM META → GateDecision）
    # =====================================================

    def build_decision_from_meta(self, meta: dict):
        """LLM 自报拒答的 META 字段 → GateDecision。

        Args:
            meta: LLM 返回的 META 注释，期望含 reason / confidence / citations

        Returns:
            GateDecision(passed=False, reason=..., layer="generation", score=confidence, ...)
        """
        from backend.rag.evidence_gate import GateDecision, RejectReason
        raw_reason = (meta.get("reason") or "no_evidence").lower()
        try:
            reason = RejectReason(raw_reason)
        except ValueError:
            reason = RejectReason.NO_EVIDENCE
        confidence = float(meta.get("confidence", 0.0))
        return GateDecision(
            passed=False,
            reason=reason,
            layer="generation",
            score=confidence,
            diagnostics={
                "meta_confidence": confidence,
                "meta_citations": meta.get("citations", []),
                "threshold": {"meta_min_confidence": 0.5},
            },
        )


__all__ = ["EvidenceGateController"]
