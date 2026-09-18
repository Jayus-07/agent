"""test_metadata_schema.py — 统一抽取 Schema v1（metadata_schema.py）。

覆盖（规划阶段 2.1 完成标准）：
  1. 枚举治理：DOC_TYPES 与 DOC_TYPE_RULES 一一映射、DOMAINS 与 DOMAIN_RULES
     一一映射（新增类型必须走 schema 演进流程，禁止只改一处）；
  2. UnifiedMetadata 校验行为与 parse_extract_response 既有契约逐条对齐；
  3. risk 字段：缺省 none、非法值归一；
  4. to_extract_dict 键集 = 旧契约 7 键 + risk。
"""
import pytest
from pydantic import ValidationError

from backend.rag.preprocessing.domain_data import DOMAIN_RULES, DOC_TYPE_RULES
from backend.rag.preprocessing.metadata_schema import (
    DOMAINS,
    DOC_TYPES,
    KEYWORDS_MAX,
    TIME_REFS_MAX,
    RiskInfo,
    UnifiedMetadata,
)

GOOD = {
    "doc_type": "financial",
    "confidence": 0.9,
    "business_domain": "finance",
    "summary": "本季度报销与预算执行情况说明。",
    "keywords": ["报销", "预算", "发票"],
    "entities": {"person": ["张三"], "org": ["财务部"]},
    "time_refs": ["2026年Q3"],
    "risk": {"level": "medium", "signals": ["预算超支"]},
}

OLD_CONTRACT_KEYS = {"doc_type", "confidence", "business_domain", "summary",
                     "keywords", "entities", "time_refs"}


# ============ 枚举治理（阶段 2.1 完成标准的机器化） ============

class TestTaxonomyConsistency:
    def test_doc_types_mapped_to_rules(self):
        """Schema 枚举必须覆盖规则词表全部类型 + general 兜底，一一映射。"""
        assert set(DOC_TYPES) == set(DOC_TYPE_RULES.keys()) | {"general"}

    def test_domains_mapped_to_rules(self):
        assert set(DOMAINS) == set(DOMAIN_RULES.keys()) | {"general"}

    def test_no_duplicate_doc_types(self):
        assert len(DOC_TYPES) == len(set(DOC_TYPES))


# ============ UnifiedMetadata 校验 ============

class TestUnifiedMetadata:
    def test_valid_full(self):
        m = UnifiedMetadata.model_validate(GOOD)
        assert m.doc_type == "financial"
        assert m.risk.level == "medium"
        d = m.to_extract_dict()
        assert OLD_CONTRACT_KEYS <= set(d)
        assert "risk" in d
        assert d["risk"] == {"level": "medium", "signals": ["预算超支"]}

    def test_doc_type_out_of_enum_rejected(self):
        with pytest.raises(ValidationError):
            UnifiedMetadata.model_validate(dict(GOOD, doc_type="hacking"))

    def test_doc_type_normalized(self):
        m = UnifiedMetadata.model_validate(dict(GOOD, doc_type="  FINANCIAL "))
        assert m.doc_type == "financial"

    def test_confidence_dirty_value_falls_back(self):
        m = UnifiedMetadata.model_validate(dict(GOOD, confidence="not-a-number"))
        assert m.confidence == 0.7

    def test_confidence_clamped_and_rounded(self):
        m = UnifiedMetadata.model_validate(dict(GOOD, confidence=1.23456))
        assert m.confidence == 1.0
        m2 = UnifiedMetadata.model_validate(dict(GOOD, confidence=-0.5))
        assert m2.confidence == 0.0

    def test_keywords_coercion_and_cap(self):
        m = UnifiedMetadata.model_validate(
            dict(GOOD, keywords=[1, 2, "  ", "ok"] + [f"k{i}" for i in range(20)]))
        assert m.keywords[0] == "1"
        assert "ok" in m.keywords
        assert len(m.keywords) == KEYWORDS_MAX

    def test_entities_non_list_value_dropped(self):
        m = UnifiedMetadata.model_validate(
            dict(GOOD, entities={"person": "not-a-list", "org": ["a", ""]}))
        assert m.entities == {"org": ["a"]}

    def test_time_refs_none_to_empty(self):
        m = UnifiedMetadata.model_validate(dict(GOOD, time_refs=None))
        assert m.time_refs == []
        assert len(m.time_refs) <= TIME_REFS_MAX

    def test_business_domain_empty_falls_back_general(self):
        m = UnifiedMetadata.model_validate(dict(GOOD, business_domain=""))
        assert m.business_domain == "general"


# ============ risk 字段 ============

class TestRiskInfo:
    def test_default_is_none(self):
        r = RiskInfo()
        assert r.level == "none"
        assert r.signals == []

    def test_missing_risk_field_defaults(self):
        m = UnifiedMetadata.model_validate({k: v for k, v in GOOD.items() if k != "risk"})
        assert m.risk.level == "none"

    def test_invalid_level_normalized_to_none(self):
        assert RiskInfo(level="critical").level == "none"
        assert RiskInfo(level=None).level == "none"
        assert RiskInfo(level="HIGH").level == "high"

    def test_signals_dirty_values_dropped(self):
        r = RiskInfo(signals=["合规", "  ", "", 42, None])
        assert r.signals == ["合规", "42"]
