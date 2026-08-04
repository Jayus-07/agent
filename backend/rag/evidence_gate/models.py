"""Evidence Gate 领域模型 — 纯数据结构，无业务逻辑。

从 __init__.py 抽出，便于独立导入和测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =====================================================
# 拒答原因 (5 类，对齐企业实践 §0.3)
# =====================================================

class RejectReason(str, Enum):
    NO_EVIDENCE = "no_evidence"                  # 空召回 / 完全没匹配
    LOW_RELEVANCE = "low_relevance"              # 召回但 top1/topK 分数过低
    DOC_TYPE_MISMATCH = "doc_type_mismatch"      # 召回 doc_type 与 QueryAnalyzer 不符
    INSUFFICIENT = "insufficient"                # Rerank 多维分数综合判定
    HALLUCINATION = "hallucination"              # Faithfulness/Groundedness 校验失败


# 中文 user-facing 提示语 (按层分类)
# 注意：self_correction_attempted 由 build_rejection_response 追加，
# 这里保持纯描述，避免重复语义。
REJECT_MESSAGES: dict[RejectReason, str] = {
    RejectReason.NO_EVIDENCE: "知识库暂无相关资料。",
    RejectReason.LOW_RELEVANCE: "已检索到部分内容，但相关性不足以可靠回答，请尝试换一种提问方式。",
    RejectReason.DOC_TYPE_MISMATCH: "已检索到相关内容，但文档类型与问题不匹配。",
    RejectReason.INSUFFICIENT: "召回的多条证据之间支撑不充分。",
    RejectReason.HALLUCINATION: "已生成的答案中包含未经资料支撑的事实，已自动拒答以避免错误信息。",
}


# =====================================================
# 决策结构
# =====================================================

@dataclass
class GateDecision:
    """单个 Evidence Gate 的判定结果。

    Diagnostics 字段统一为 dict，便于序列化到 Trace span.metrics。
    """
    passed: bool
    reason: Optional[RejectReason] = None
    layer: str = ""                              # retrieval | rerank | evaluation
    score: float = 0.0
    diagnostics: dict = field(default_factory=dict)

    def to_metrics(self) -> dict:
        """转 dict 供 trace_collector.end_span(metrics=...) 用。"""
        m = {
            "gate_passed": self.passed,
            "gate_layer": self.layer,
            "gate_score": round(self.score, 4),
        }
        if self.reason is not None:
            m["gate_reason"] = self.reason.value
        m.update(self.diagnostics)
        return m


@dataclass
class RejectInfo:
    """嵌入到 TraceRecord.metadata 的拒答诊断 (不破坏 SQLite 现有 schema)。"""
    rejected: bool = False
    layer: str = ""
    reason: Optional[str] = None
    scores: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    suggested_queries: list = field(default_factory=list)
    self_correction_attempted: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "rejected": self.rejected,
            "layer": self.layer,
            "reason": self.reason,
            "scores": self.scores,
            "thresholds": self.thresholds,
            "suggested_queries": self.suggested_queries,
            "self_correction_attempted": self.self_correction_attempted,
            "timestamp": self.timestamp,
        }


# =====================================================
# 风险等级矩阵（替代 §0.3 表里的虚构 intent §C1 修复）
# =====================================================

# 高风险 doc_type（命中则风险升级到 high）
HIGH_RISK_DOC_TYPES = {"policy", "compliance", "legal"}

# intent → 默认风险等级（与现有 classify_intent 的 7 个值对齐）
INTENT_RISK_LEVEL: dict[str, str] = {
    "entity_query":    "low",
    "order_query":     "medium",
    "inventory_query": "low",
    "ad_query":        "low",
    "fact_query":      "medium",   # 财务/人事事实查询可能落在此
    "report_query":    "medium",
    "summary_query":   "low",
}


__all__ = [
    "RejectReason",
    "REJECT_MESSAGES",
    "GateDecision",
    "RejectInfo",
    "HIGH_RISK_DOC_TYPES",
    "INTENT_RISK_LEVEL",
]
