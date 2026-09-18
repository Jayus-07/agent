"""metadata_schema.py — 元数据统一抽取 JSON Schema v1（Pydantic v2）。

规划（docs/2026-09-19-RAG元数据管道统一抽取与级联路由上线规划.md）阶段 2.1：
统一 schema 是唯一抽取契约——LLM 输出、级联路由各层产物、基线评估脚本
的预测都校验到同一模型上。

枚举治理：doc_type 枚举与规则词表 domain_data.DOC_TYPE_RULES 一一映射，
由 tests/rag/test_metadata_schema.py 一致性测试锁定；新增类型必须同时改
DOC_TYPES 与 DOC_TYPE_RULES（schema 演进流程，规划 §2.2 N6），禁止只改一处。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ── taxonomy 枚举（单一映射事实源，与 DOC_TYPE_RULES 的 keys 测试锁定）──
# general 是兜底类，不属于 DOC_TYPE_RULES（规则链对 general 零正则）
DOC_TYPES: tuple[str, ...] = (
    "listing", "sop", "ad_policy", "faq", "product_spec", "training",
    "policy", "compliance", "legal", "security", "financial",
    "customer_data", "contract_template", "general",
)

# business_domain 枚举（与 DOMAIN_RULES keys 一致性测试锁定；general 为兜底）
DOMAINS: tuple[str, ...] = (
    "product", "order", "inventory", "logistics", "advertising",
    "customer", "supplier", "analytics", "data", "financial", "general",
)

# 上下限口径（对齐 metadata_llm.parse_extract_response 既有行为）
KEYWORDS_MAX = 10
TIME_REFS_MAX = 20
DEFAULT_CONFIDENCE = 0.7


class RiskInfo(BaseModel):
    """风险标记（Schema v1 新增字段）：LLM 未输出时缺省 none，解析端永远兼容。"""
    level: str = "none"        # high | medium | low | none
    signals: list[str] = Field(default_factory=list)

    @field_validator("level", mode="before")
    @classmethod
    def _norm_level(cls, v: Any) -> str:
        s = str(v or "none").strip().lower()
        return s if s in ("high", "medium", "low", "none") else "none"

    @field_validator("signals", mode="before")
    @classmethod
    def _norm_signals(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v
                if x is not None and str(x).strip()][:20]


class UnifiedMetadata(BaseModel):
    """统一抽取结果模型：doc_type / confidence / business_domain / summary /
    keywords / entities / time_refs / risk。

    校验策略与 parse_extract_response 既有行为逐条对齐：脏值宽容回落
    （confidence 坏值 → 0.7），结构性错误严格拒绝（doc_type 越界 → ValueError）。
    """
    doc_type: str
    confidence: float = DEFAULT_CONFIDENCE
    business_domain: str = "general"
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(default_factory=dict)
    time_refs: list[str] = Field(default_factory=list)
    risk: RiskInfo = Field(default_factory=RiskInfo)

    @field_validator("doc_type", mode="before")
    @classmethod
    def _norm_doc_type(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        if s not in DOC_TYPES:
            raise ValueError(f"doc_type out of enum: {s!r}")
        return s

    @field_validator("confidence", mode="before")
    @classmethod
    def _norm_confidence(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return DEFAULT_CONFIDENCE
        return min(max(f, 0.0), 1.0)

    @field_validator("business_domain", mode="before")
    @classmethod
    def _norm_domain(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s or "general"

    @field_validator("keywords", mode="before")
    @classmethod
    def _norm_keywords(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(k).strip() for k in v if str(k).strip()][:KEYWORDS_MAX]

    @field_validator("entities", mode="before")
    @classmethod
    def _norm_entities(cls, v: Any) -> dict[str, list[str]]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, list[str]] = {}
        for k, val in v.items():
            if not isinstance(val, list):
                continue
            items = [str(x) for x in val if str(x).strip()]
            if items:
                out[str(k)] = items
        return out

    @field_validator("time_refs", mode="before")
    @classmethod
    def _norm_time_refs(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(t).strip() for t in v if str(t).strip()][:TIME_REFS_MAX]

    @model_validator(mode="after")
    def _round_confidence(self) -> "UnifiedMetadata":
        self.confidence = round(self.confidence, 2)
        return self

    def to_extract_dict(self) -> dict:
        """转 dict：与 metadata_llm.parse_extract_response 既有返回契约兼容，
        另带 risk 字段（新）。"""
        return {
            "doc_type": self.doc_type,
            "confidence": self.confidence,
            "business_domain": self.business_domain,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "entities": {k: list(v) for k, v in self.entities.items()},
            "time_refs": list(self.time_refs),
            "risk": self.risk.model_dump(),
        }
