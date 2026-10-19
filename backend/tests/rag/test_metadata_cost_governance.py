"""元数据增强成本、规则分类权威与精确实体回归。"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.stages.metadata_stage import MetadataStage


def _stage() -> MetadataStage:
    registry = MagicMock()
    registry.list_all.return_value = {}
    return MetadataStage(registry, embedding=None, department="general")


def _patch_rules(monkeypatch, *, doc_type: str = "policy") -> None:
    import backend.config.rag as rag_config
    import backend.rag.preprocessing.entity as entity_module
    import backend.rag.preprocessing.keyword as keyword_module
    import backend.rag.preprocessing.metadata as metadata_module

    monkeypatch.setattr(rag_config, "ENABLE_LLM_METADATA_EXTRACT", True)
    monkeypatch.setattr(
        metadata_module, "classify_with_confidence",
        lambda *args, **kwargs: (doc_type, 0.91, {"scores": {doc_type: 9}}),
    )
    monkeypatch.setattr(
        metadata_module, "detect_business_domain",
        lambda *args, **kwargs: ("hr", [], {"scores": {"hr": 4}}),
    )
    monkeypatch.setattr(metadata_module, "extract_time_refs", lambda _text: [])
    monkeypatch.setattr(entity_module, "extract_entities", lambda _text: {})
    monkeypatch.setattr(keyword_module, "extract_rule_keywords", lambda *args, **kwargs: [])


def test_rule_classification_wins_over_conflicting_llm_fields(monkeypatch):
    _patch_rules(monkeypatch)
    import backend.rag.preprocessing.metadata_llm as metadata_llm

    async def _conflicting_result(*args, **kwargs):
        return {
            "doc_type": "faq",
            "confidence": 0.99,
            "business_domain": "finance",
            "summary": "本制度说明 SKU-900 的审批方式。",
            "keywords": ["员工", "SKU-900", "申请"],
            "entities": {},
            "llm_tokens": {"prompt_tokens": 12, "completion_tokens": 8},
            "provider_called": True,
            "actual_model": "mock-model",
        }

    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", _conflicting_result)
    text = ("本制度说明 SKU-900 的审批方式。\n" * 250)
    result = asyncio.run(_stage().build(text, {"doc_id": "d1"}))

    assert result["doc_type"] == "policy"
    assert result["confidence"] == 0.91
    assert result["business_domain"] == "hr"
    assert [item["word"] for item in result["keywords_llm"]] == ["SKU-900"]
    assert result["llm_used"] is True


def test_faq_skips_metadata_and_simulated_question_provider_calls(monkeypatch):
    _patch_rules(monkeypatch, doc_type="faq")
    import backend.config.rag as rag_config
    import backend.rag.preprocessing.metadata_llm as metadata_llm
    import backend.rag.preprocessing.question_gen as question_gen

    monkeypatch.setattr(rag_config, "ENABLE_SIMULATED_QUESTIONS", True)

    async def _unexpected_metadata_call(*args, **kwargs):
        raise AssertionError("FAQ 不应调用元数据模型")

    def _unexpected_questions(*args, **kwargs):
        raise AssertionError("结构化 FAQ 不应调用问题生成模型")

    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", _unexpected_metadata_call)
    monkeypatch.setattr(question_gen, "generate_chunk_questions", _unexpected_questions)
    text = "Q: 年假如何申请？\nA: 在系统提交。\nQ: 病假如何登记？\nA: 上传证明。"
    result = asyncio.run(_stage().build(
        text, {"doc_id": "faq-1"}, chunks_text=["Q: 年假如何申请？\nA: 在系统提交。"],
    ))

    assert result["doc_type"] == "faq"
    assert result["llm_used"] is False
    assert result["llm_tokens"] == {}
    assert result["questions_by_chunk"] == []


def test_metadata_failure_uses_extractive_summary_without_second_llm_call(monkeypatch):
    _patch_rules(monkeypatch)
    import backend.rag.preprocessing.metadata_llm as metadata_llm

    calls = []

    async def _unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", _unavailable)
    text = ("第一句说明 SKU-42 的使用规范。第二句补充审批条件。\n" * 140)
    result = asyncio.run(_stage().build(text, {"doc_id": "d2"}))

    assert len(calls) == 1
    assert result["llm_used"] is False
    assert result["summary"].startswith("第一句说明 SKU-42")


def test_failed_metadata_call_usage_is_kept_in_processing_lineage(monkeypatch):
    _patch_rules(monkeypatch)
    import backend.rag.preprocessing.metadata_llm as metadata_llm
    from backend.rag.indexing.processing_lineage import ProcessingRunContext
    from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder

    async def _invalid_output(*args, **kwargs):
        metadata_llm._set_last_metadata_call_meta(
            provider_call_attempted=True,
            provider_called=True,
            provider_duration_ms=37,
            llm_tokens={"prompt_tokens": 19, "completion_tokens": 6},
            llm_usage_status="reported",
            actual_model="mock-model",
            prompt_version="v2",
            prompt_source="db",
            prompt_hash="abc123",
            fallback_reason="invalid_output",
        )
        return None

    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", _invalid_output)
    context = ProcessingRunContext.create("doc-lineage", "hash-lineage", "upload")
    recorder = ProcessingRunRecorder(context)
    text = "这份长文档触发元数据抽取。\n" * 180

    asyncio.run(_stage().build(
        text,
        {"doc_id": "doc-lineage", "source_file": "policy.md"},
        processing_recorder=recorder,
    ))

    step = next(item for item in context.snapshot().steps if item.stage == "metadata_extract")
    assert step.status == "fallback"
    assert step.fallback_reason == "invalid_output"
    assert step.prompt_tokens == 19
    assert step.completion_tokens == 6
    assert step.model_name == "mock-model"
    assert step.prompt_version == "v2"
    assert step.metadata["provider_call"] is True
    assert step.metadata["provider_call_attempted"] is True
    assert step.metadata["provider_duration_ms"] == 37


def test_question_generation_provider_call_counts_as_llm_use(monkeypatch):
    _patch_rules(monkeypatch)
    import backend.config.rag as rag_config
    import backend.rag.preprocessing.question_gen as question_gen

    monkeypatch.setattr(rag_config, "ENABLE_LLM_METADATA_EXTRACT", False)
    monkeypatch.setattr(rag_config, "ENABLE_SIMULATED_QUESTIONS", True)
    monkeypatch.setattr(
        question_gen,
        "generate_chunk_questions",
        lambda chunks, doc_type: ([[] for _ in chunks], {
            "prompt_tokens": 11,
            "completion_tokens": 5,
        }),
    )
    monkeypatch.setattr(
        question_gen,
        "get_last_generation_meta",
        lambda: {"status": "success", "provider_call": True},
    )

    result = asyncio.run(_stage().build(
        "短文档中有一个可检索的规定。",
        {"doc_id": "question-gen-only"},
        chunks_text=["短文档中有一个可检索的规定。"],
    ))

    assert result["llm_used"] is True
    assert result["llm_strategy"] == "question_gen"
    assert result["llm_tokens"]["prompt_tokens"] == 11
    assert result["llm_tokens"]["completion_tokens"] == 5


def test_exact_entity_filter_rejects_topic_words_and_unsupported_candidates():
    from backend.rag.preprocessing.keyword import filter_exact_llm_keywords

    text = "员工申请年假时，请检查 SKU-900 和 GB/T 4944 标准编号。"
    result = filter_exact_llm_keywords(
        text, ["申请", "员工", "SKU-900", "GB/T 4944", "SO-404不存在"],
    )
    assert result == ["SKU-900", "GB/T 4944"]


def test_identifier_extractor_covers_sku_model_error_order_and_standard_codes():
    from backend.rag.preprocessing.keyword import extract_identifiers

    values = extract_identifiers(
        "SKU: AX-20，型号 ZX900，错误码 E-404，订单号 SO20241002，标准 GB/T 4944。"
    )
    assert {"AX-20", "ZX900", "E-404", "SO20241002", "GB/T 4944"}.issubset(values)
