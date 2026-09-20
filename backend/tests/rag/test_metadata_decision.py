"""R0/R1/LLM/fallback 统一决策契约测试。"""

import pytest

from backend.rag.preprocessing import metadata_runtime
from backend.rag.preprocessing.metadata_classifier import ClassifierPrediction
from backend.rag.preprocessing.metadata_decision import decide_metadata


@pytest.mark.asyncio
async def test_r0_accepts_only_unique_strong_signal():
    result = await decide_metadata("固定编号合同 第一条 适用范围", "approved-contract-id.docx")

    assert result.source == "r0"
    assert result.decision == "accepted"
    assert result.doc_type == "legal"
    assert result.llm_call_count == 0


@pytest.mark.asyncio
async def test_r0_skips_cache_io(monkeypatch):
    """确定性 R0 命中不应为一次零成本决策访问 Redis。"""
    async def _cache_io_must_not_run(*args, **kwargs):
        raise AssertionError("R0 命中不应访问元数据决策缓存")

    monkeypatch.setattr(
        metadata_runtime,
        "get_cached_decision_async",
        _cache_io_must_not_run,
    )
    monkeypatch.setattr(
        metadata_runtime,
        "put_cached_decision_async",
        _cache_io_must_not_run,
    )

    result = await decide_metadata(
        "固定编号合同 第一条 适用范围",
        "approved-contract-id-cache-bypass.docx",
    )

    assert result.source == "r0"
    assert result.llm_call_count == 0


async def _fake_llm_result(*args, **kwargs):
    return {
        "doc_type": "legal",
        "confidence": 0.9,
        "business_domain": "general",
        "summary": "s",
        "keywords": [],
        "entities": {},
        "time_refs": [],
        "risk": {"level": "none", "signals": []},
        "prompt_version": "default",
    }


async def _low_confidence_prediction(*args, **kwargs):
    return ClassifierPrediction(
        label="legal",
        confidence=0.55,
        candidates=[("legal", 0.55), ("policy", 0.54)],
        accepted=False,
        abstain_reason="below_class_threshold",
        model_version="test-model",
        feature_version="test-features",
    )


@pytest.mark.asyncio
async def test_r1_abstain_falls_to_single_llm_call(monkeypatch):
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision._classifier_prediction",
        _low_confidence_prediction,
    )
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async",
        _fake_llm_result,
    )

    result = await decide_metadata("模糊正文", "unknown.docx", embedding=object())

    assert result.source == "llm"
    assert result.decision == "accepted"
    assert result.llm_call_count == 1


@pytest.mark.asyncio
async def test_llm_usage_is_carried_in_decision_envelope(monkeypatch):
    """LLM 成功时，调用用量必须跟随统一决策契约进入血缘层。"""
    async def _no_classifier(*args, **kwargs):
        return None

    async def _fake_llm(*args, **kwargs):
        result = await _fake_llm_result()
        result.update({
            "actual_model": "qwen3.7-plus@tp",
            "llm_tokens": {
                "prompt_tokens": 101,
                "completion_tokens": 9,
                "total_tokens": 110,
                "cached_tokens": 3,
                "cost_usd": 0.001,
            },
        })
        return result

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision._classifier_prediction",
        _no_classifier,
    )
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async",
        _fake_llm,
    )

    result = await decide_metadata(
        "没有稳定类型证据的普通正文-usage-envelope",
        "usage-envelope.md",
    )

    assert result.source == "llm"
    assert result.metadata["actual_model"] == "qwen3.7-plus@tp"
    assert result.metadata["llm_tokens"]["total_tokens"] == 110


@pytest.mark.asyncio
async def test_llm_failure_uses_complete_deterministic_fallback(monkeypatch):
    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async",
        _none,
    )
    result = await decide_metadata("无法判断的正文", "unknown.docx", embedding=None)

    assert result.source == "fallback"
    assert result.decision in {"fallback", "review"}
    assert result.doc_type == "general"
    assert result.fallback_reason == "llm_unavailable"
    assert result.llm_call_count == 1


@pytest.mark.asyncio
async def test_cache_hit_skips_metadata_llm(monkeypatch):
    class _Cache:
        def __init__(self):
            self.values = {}

        def get_json(self, key):
            return self.values.get(key)

        def set_json(self, key, value, ttl=None):
            self.values[key] = value

    calls = {"llm": 0}

    async def _fake_llm(*args, **kwargs):
        calls["llm"] += 1
        return await _fake_llm_result()

    async def _no_classifier(*args, **kwargs):
        return None

    monkeypatch.setattr(metadata_runtime, "_metadata_cache", _Cache())
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision._classifier_prediction",
        _no_classifier,
    )
    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_decision.extract_metadata_llm_async",
        _fake_llm,
    )

    first = await decide_metadata("没有强类型证据的普通正文", "cache-hit.docx")
    second = await decide_metadata("没有强类型证据的普通正文", "cache-hit.docx")

    assert first.source == "llm"
    assert second.source == "llm"
    assert calls["llm"] == 1


def test_keyword_fallback_can_explicitly_disable_llm(monkeypatch):
    from backend.rag.preprocessing import keyword

    def _boom(*args, **kwargs):
        raise AssertionError("deterministic fallback must not call keyword LLM")

    monkeypatch.setattr(keyword, "extract_doc_keywords_llm", _boom)
    result = keyword.extract_doc_keywords_typed(
        "普通正文", doc_type="faq", allow_llm=False
    )

    assert result.llm_keywords == []
    assert result.llm_tokens == {}
    assert result.llm_strategy == "deterministic"
