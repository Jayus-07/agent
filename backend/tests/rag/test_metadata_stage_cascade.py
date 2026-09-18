"""test_metadata_stage_cascade.py — MetadataStage 级联接线（规划阶段 2.2 接入点）。

覆盖：
  1. finalize_cascade：返回契约与 finalize_unified 完全一致（键集），
     llm_used=False、llm_strategy=cascade_L{N}、零 LLM 调用；
  2. build() 接线：METADATA_CASCADE_ENABLED=true 且 L0 命中 → 不调 LLM 抽取；
  3. build() 接线：级联关闭时行为与旧链一致（extract_metadata_llm_async 被调）。
"""
import pytest

from backend.rag.preprocessing.metadata_router import CascadeDecision
from backend.rag.indexing.stages.metadata_stage import MetadataStage

CASCADE_KEYS_ADDED = {"llm_strategy", "llm_decision"}


class _FakeRegistry:
    def list_all(self):
        return {}


class _FakeEmbedding:
    model_name = "fake-embed"


@pytest.fixture
def _stage():
    return MetadataStage(_FakeRegistry(), _FakeEmbedding(), department="qa")


_TEXT = ("本合同由甲方与乙方签订，第一条 违约责任；第二条 保密条款；"
         "第三条 知识产权；第四条 争议解决。2026年签署。")
_META = {"source_file": "供应商NDA模板.docx", "file_path": "", "doc_id": "d1"}


def _l0_decision() -> CascadeDecision:
    return CascadeDecision(level="L0", doc_type="legal", confidence=0.95,
                           evidence={"filename_hits": ["NDA"], "domain": "order"})


@pytest.mark.asyncio
async def test_finalize_cascade_contract(_stage):
    out = await _stage.finalize_cascade(_TEXT, _META, _l0_decision())
    # 契约键集 = 规则路径 build() 的全部键（下游 chunk 注入/doc_db/registry 零感知）
    assert {"doc_type", "confidence", "business_domain", "summary", "doc_keywords",
            "keywords_rule", "keywords_llm", "llm_tokens", "llm_used", "llm_strategy",
            "llm_decision", "entities", "minhash_sig", "near_dup_id", "sections",
            "quality_score", "questions_by_chunk"} <= set(out)
    assert out["doc_type"] == "legal"
    assert out["llm_used"] is False, "级联命中零 LLM 成本"
    assert out["llm_strategy"] == "cascade_L0"
    assert out["llm_decision"]["source"] == "cascade_router"
    assert out["business_domain"] == "order", "business_domain 来自规则域识别"
    assert out["keywords_llm"] == [] and out["keywords_rule"], "结构字段为规则产物"


@pytest.mark.asyncio
async def test_build_routes_via_cascade_when_enabled(_stage, monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", True)

    async def _fake_route(full_text, filename, file_path="", embedding=None,
                          parent_span_id=""):
        return _l0_decision()

    called = {"extract": 0}

    async def _must_not_call(*a, **kw):
        called["extract"] += 1
        return {}

    monkeypatch.setattr("backend.rag.preprocessing.metadata_router.cascade_route",
                        _fake_route)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _must_not_call)
    out = await _stage.build(_TEXT, _META)
    assert out["doc_type"] == "legal"
    assert out["llm_strategy"] == "cascade_L0"
    assert called["extract"] == 0, "L0 命中时不得触发 LLM 抽取"


@pytest.mark.asyncio
async def test_build_cascade_disabled_keeps_legacy_path(_stage, monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)

    called = {"extract": 0}

    async def _fake_extract(full_text, filename, parent_span_id=""):
        called["extract"] += 1
        return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
                "summary": "s", "keywords": [], "entities": {}, "time_refs": []}

    async def _must_not_route(*a, **kw):
        raise AssertionError("级联关闭时不得进入级联路由")

    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_router.cascade_route",
                        _must_not_route)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _fake_extract)
    out = await _stage.build(_TEXT, _META)
    assert called["extract"] == 1
    assert out["doc_type"] == "legal"
    assert out["llm_strategy"] == "unified_extract", "关闭级联 = 旧统一抽取路径"


@pytest.mark.asyncio
async def test_build_l3_failure_falls_back_to_rule_path(_stage, monkeypatch):
    """级联开启 + L3 失败 → 规则链 fallback（规划阶段 4.2，行为与旧链一致）。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", True)

    async def _fake_route(full_text, filename, file_path="", embedding=None,
                          parent_span_id=""):
        return CascadeDecision(level="L3", doc_type="", confidence=0.0,
                               evidence={}, llm_result=None)

    monkeypatch.setattr("backend.rag.preprocessing.metadata_router.cascade_route",
                        _fake_route)
    out = await _stage.build(_TEXT, _META)
    assert out["doc_type"], "规则链 fallback 必须给出 doc_type"
    assert out.get("llm_strategy") != "cascade_L3"
