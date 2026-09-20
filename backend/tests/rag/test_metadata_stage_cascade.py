"""test_metadata_stage_cascade.py — MetadataStage 级联接线（规划阶段 2.2 接入点）。

覆盖：
  1. finalize_cascade：返回契约与 finalize_unified 完全一致（键集），
     llm_used=False、llm_strategy=cascade_L{N}、零 LLM 调用；
  2. build() 接线：METADATA_CASCADE_ENABLED=true 且 L0 命中 → 不调 LLM 抽取；
  3. build() 接线：级联关闭时行为与旧链一致（extract_metadata_llm_async 被调）。
"""
import pytest
from contextlib import nullcontext

from backend.rag.preprocessing.metadata_router import CascadeDecision
from backend.rag.preprocessing.metadata_schema import DecisionEnvelope
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
    assert out["llm_strategy"] == "r0"
    assert called["extract"] == 0, "L0 命中时不得触发 LLM 抽取"


@pytest.mark.asyncio
async def test_build_cascade_dispatches_shadow_for_decision(_stage, monkeypatch):
    """级联主路径也必须提交影子，且提交不能改变主决策。"""
    from backend.rag.preprocessing.metadata_schema import DecisionEnvelope

    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", True)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)

    async def _fake_decide(*args, **kwargs):
        return DecisionEnvelope(
            decision="accepted",
            doc_type="legal",
            business_domain="order",
            confidence=0.95,
            source="r0",
        )

    captured = []

    def _capture_shadow(envelope, full_text, filename, file_path):
        captured.append((envelope, full_text, filename, file_path))

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.decide_metadata",
        _fake_decide,
    )
    monkeypatch.setattr(_stage, "_dispatch_shadow_nonblocking", _capture_shadow)

    out = await _stage.build(_TEXT, _META)

    assert out["doc_type"] == "legal"
    assert out["llm_used"] is False
    assert len(captured) == 1
    assert captured[0][0].source == "r0"
    assert captured[0][2] == _META["source_file"]


@pytest.mark.asyncio
async def test_build_cascade_forwards_llm_usage_to_lineage(_stage, monkeypatch):
    """级联 LLM 成功后的 token 不能在 DecisionEnvelope→stage 时丢失。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", True)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", False)

    async def _fake_decide(*args, **kwargs):
        return DecisionEnvelope(
            decision="accepted",
            doc_type="general",
            business_domain="general",
            confidence=0.8,
            source="llm",
            metadata={
                "actual_model": "qwen3.7-plus@tp",
                "llm_tokens": {
                    "prompt_tokens": 101,
                    "completion_tokens": 9,
                    "total_tokens": 110,
                },
            },
            prompt_version="v3",
        )

    class _Recorder:
        def __init__(self):
            self.finished = []

        def begin_stage(self, *args, **kwargs):
            return ("metadata-step", 0.0)

        def bind_stage(self, *args, **kwargs):
            return nullcontext()

        def set_stage_model(self, *args, **kwargs):
            pass

        def finish_stage(self, step_id, **kwargs):
            self.finished.append((step_id, kwargs))

    async def _fake_finalize(*args, **kwargs):
        return {"doc_type": "general", "llm_used": True}

    recorder = _Recorder()
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.decide_metadata",
        _fake_decide,
    )
    monkeypatch.setattr(_stage, "finalize_decision", _fake_finalize)

    await _stage.build(_TEXT, _META, processing_recorder=recorder)

    assert recorder.finished[0][1]["usage"]["total_tokens"] == 110


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


# ============ 影子采集接线（阶段 5 基建） ============

@pytest.mark.asyncio
async def test_build_shadow_runs_on_main_path_success(_stage, monkeypatch):
    """影子开启：主路径统一抽取成功后提交影子，且主路径不等待它。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)

    async def _fake_extract(full_text, filename, parent_span_id=""):
        return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
                "summary": "s", "keywords": [], "entities": {}, "time_refs": []}

    async def _fake_shadow(full_text, filename, file_path="", embedding=None):
        from backend.rag.preprocessing.metadata_router import CascadeDecision
        return CascadeDecision(level="L0", doc_type="legal", confidence=0.95,
                               evidence={"domain": "order"})

    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _fake_extract)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_router.shadow_route",
                        _fake_shadow)
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_shadow.submit_shadow_job",
        lambda *a, **k: "shadow-test-1",
    )
    out = await _stage.build(_TEXT, _META)
    assert out["doc_type"] == "legal" and out["llm_used"] is True, "影子不得改变主路径结果"


@pytest.mark.asyncio
async def test_build_shadow_disabled_skips(_stage, monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", False)

    async def _fake_extract(full_text, filename, parent_span_id=""):
        return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
                "summary": "s", "keywords": [], "entities": {}, "time_refs": []}

    async def _must_not_shadow(*a, **kw):
        raise AssertionError("影子关闭时不得采集")

    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _fake_extract)
    monkeypatch.setattr("backend.rag.preprocessing.metadata_router.shadow_route",
                        _must_not_shadow)
    out = await _stage.build(_TEXT, _META)
    assert out["doc_type"] == "legal"


@pytest.mark.asyncio
async def test_build_shadow_failure_never_breaks_main_path(_stage, monkeypatch):
    """影子投递抛异常必须被吞掉，主路径照常返回。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)

    async def _fake_extract(full_text, filename, parent_span_id=""):
        return {"doc_type": "legal", "confidence": 0.9, "business_domain": "general",
                "summary": "s", "keywords": [], "entities": {}, "time_refs": []}

    def _boom(*args, **kwargs):
        raise RuntimeError("shadow broker down")

    monkeypatch.setattr("backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
                        _fake_extract)
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_shadow.submit_shadow_job", _boom
    )
    out = await _stage.build(_TEXT, _META)
    assert out["doc_type"] == "legal", "影子故障不得影响主路径"


@pytest.mark.asyncio
async def test_decision_fallback_has_complete_contract_and_no_hidden_llm(_stage, monkeypatch):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", True)

    async def _boom(*args, **kwargs):
        raise AssertionError("fallback must not invoke metadata LLM")

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async",
        _boom,
    )
    monkeypatch.setattr(
        "backend.rag.preprocessing.keyword.extract_doc_keywords_llm",
        _boom,
    )

    out = await _stage.build(
        "普通会议纪要内容，没有稳定类型证据。",
        {"source_file": "unknown.docx", "file_path": "", "doc_id": "d2"},
    )
    required = {
        "doc_type", "confidence", "business_domain", "summary", "doc_keywords",
        "keywords_rule", "keywords_llm", "entities", "time_refs", "risk",
        "llm_used", "llm_strategy", "llm_decision", "metadata_fingerprint",
        "sections", "quality_score", "minhash_sig", "near_dup_id", "department",
        "questions_by_chunk", "decision_envelope", "fallback_reason",
    }
    assert required <= set(out)
    assert out["llm_strategy"] == "fallback"
    assert out["llm_used"] is False
    assert out["fallback_reason"] == "llm_unavailable"
