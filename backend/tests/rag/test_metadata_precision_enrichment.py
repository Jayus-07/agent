from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.rag.indexing.processing_lineage import ProcessingRunContext
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder
from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.rag.preprocessing.keyword import extract_precise_keywords


def test_precise_keyword_candidates_require_source_match_and_format_evidence():
    text = (
        "型号: AX-2048，错误码 ERR-503，产品名为 iPhone15Pro。"
        "制度《员工年假管理办法》于2026年发布。FAQ 中包含年假、病假、报销、"
        "申请、处理、业务、员工和 HR。"
    )

    result = extract_precise_keywords(
        text,
        [
            "AX-2048", "ERR-503", "iPhone15Pro", "员工年假管理办法",
            "年假", "病假", "报销", "申请", "处理", "业务", "员工", "HR",
            "不存在的型号 ZX-9000",
        ],
    )

    assert {"AX-2048", "ERR-503", "iPhone15Pro", "员工年假管理办法"} <= set(result)
    assert not {"年假", "病假", "报销", "申请", "处理", "业务", "员工", "HR"} & set(result)
    assert "不存在的型号 ZX-9000" not in result


def test_precise_keywords_are_empty_without_reliable_entities():
    text = "员工申请后由业务部门处理，一般制度按现有流程执行。"

    assert extract_precise_keywords(
        text, ["员工", "申请", "业务部门", "处理", "一般制度", "流程", "HR"]
    ) == []


def test_llm_named_entity_can_cover_chinese_names_without_format_markers():
    text = "本方案由字节跳动提供支持，并适配现有员工流程。"

    result = extract_precise_keywords(
        text,
        ["员工", "流程"],
        exact_named_entities=["字节跳动", "不存在的公司"],
    )

    assert result == ["字节跳动"]


def test_precise_keywords_write_to_registry_legacy_field():
    import json

    from backend.rag.indexing.indexer import _serialize_registry_keywords

    assert json.loads(_serialize_registry_keywords({
        "doc_keywords": ["AX-2048", "字节跳动"],
        "keywords": ["过时字段值"],
    })) == ["AX-2048", "字节跳动"]
    assert json.loads(_serialize_registry_keywords({"keywords": ["历史实体"]})) == ["历史实体"]


def test_parse_and_chunk_full_reports_real_internal_stage_boundaries(tmp_path):
    from backend.rag.preprocessing.pipeline import parse_and_chunk_full

    path = tmp_path / "timing-faq.md"
    path.write_text(
        "**Q:** 如何查询订单 ORD-2026？\n**A:** 在系统中搜索订单编号。\n",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(path))

    assert chunks
    assert qc["coverage"]["uncovered_count"] == 0
    assert {
        "parse_file_ms", "clean_ms", "structure_analysis_ms",
        "classification_routing_ms", "split_ms", "chunk_postprocess_ms", "total_ms",
    } <= set(qc["stage_ms"])
    assert all(value >= 0 for value in qc["stage_ms"].values())


def test_question_generation_records_only_real_provider_invocations(monkeypatch):
    import backend.infra.llm.proxy as proxy_module
    import backend.observability.tracer as tracer_module
    import backend.rag.preprocessing.llm_enrichment as enrichment_module
    import backend.rag.preprocessing.question_gen as question_gen

    events = []

    class FakeTraceCollector:
        def start_span(self, *args, **kwargs):
            events.append(("start", args, kwargs))
            return "fake-span"

        def end_span(self, span_id, **kwargs):
            events.append(("end", span_id, kwargs))

    monkeypatch.setattr(tracer_module, "trace_collector", FakeTraceCollector())
    monkeypatch.setattr(
        enrichment_module, "invoke_metadata_llm",
        lambda *args, **kwargs: SimpleNamespace(content="ok"),
    )
    monkeypatch.setattr(
        proxy_module, "_last_call_meta_var",
        SimpleNamespace(get=lambda: {
            "model": "mock-model", "prompt_tokens": 12,
            "completion_tokens": 4, "reasoning_tokens": 0,
        }),
    )
    token = question_gen.set_trace_parent_span_id("metadata-stage")
    try:
        response = question_gen._invoke_llm("prompt")
    finally:
        question_gen.reset_trace_parent_span_id(token)

    assert response.content == "ok"
    assert events[0][0] == "start"
    assert events[0][2]["parent_id"] == "metadata-stage"
    assert events[0][2]["type"] == "llm_call"
    assert events[1][2]["metrics"]["provider_call"] is True
    assert events[1][2]["metrics"]["prompt_tokens"] == 12


def _patch_rule_fallback(monkeypatch, doc_type="general"):
    import backend.config.rag as rag_config
    import backend.rag.preprocessing.entity as entity_module
    import backend.rag.preprocessing.metadata as metadata_module
    import backend.rag.preprocessing.keyword as keyword_module

    monkeypatch.setattr(rag_config, "ENABLE_LLM_METADATA_EXTRACT", True)
    monkeypatch.setattr(rag_config, "METADATA_LLM_EXTRACT_MIN_CHARS", 1)
    monkeypatch.setattr(
        metadata_module, "classify_with_confidence",
        lambda *args, **kwargs: (doc_type, 0.91, {"source": "test_rule"}),
    )
    monkeypatch.setattr(
        metadata_module, "detect_business_domain",
        lambda *args, **kwargs: ("hr", [], {"source": "test_rule"}),
    )
    monkeypatch.setattr(
        metadata_module, "assess_quality",
        lambda text: {"score": 100, "status": "pass", "issues": [],
                      "passed": True, "dimensions": {}},
    )
    monkeypatch.setattr(metadata_module, "compute_minhash", lambda text: [1, 2, 3])
    monkeypatch.setattr(metadata_module, "extract_time_refs", lambda text: [])
    monkeypatch.setattr(metadata_module, "extract_person_names", lambda text: [])
    monkeypatch.setattr(metadata_module, "extract_sections", lambda *args, **kwargs: [])
    monkeypatch.setattr(keyword_module, "extract_rule_keywords", lambda *args, **kwargs: [])
    monkeypatch.setattr(entity_module, "extract_entities", lambda text: {})
    monkeypatch.setattr(metadata_module, "_extract_first_sentences", lambda text, n=2: "摘取式摘要")
    return metadata_module


@pytest.mark.asyncio
async def test_rule_classification_wins_and_metadata_enrichment_is_single_call(monkeypatch):
    import backend.rag.preprocessing.metadata_llm as metadata_llm

    _patch_rule_fallback(monkeypatch, doc_type="faq")
    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", AsyncMock(return_value={
        "doc_type": "policy",
        "confidence": 0.12,
        "business_domain": "financial",
        "summary": "规则分类冲突测试摘要",
        "keywords": [],
        "entities": {},
        "provider_called": True,
        "llm_tokens": {"prompt_tokens": 12, "completion_tokens": 4},
    }))

    stage = MetadataStage(registry=None, embedding=None)
    captured = {}

    async def finalize(full_text, base_meta, unified, **kwargs):
        captured.update(kwargs["rule_classification"])
        captured["unified"] = dict(unified)
        return {"doc_type": unified["doc_type"], "business_domain": unified["business_domain"]}

    monkeypatch.setattr(stage, "finalize_unified", finalize)
    result = await stage.build(
        "制度与员工问答内容。" * 300,
        {"doc_id": "doc-1", "source_file": "faq.docx", "file_path": "faq.docx"},
        chunks_text=[],
    )

    metadata_llm.extract_metadata_llm_async.assert_awaited_once()
    assert metadata_llm.extract_metadata_llm_async.await_args.kwargs["include_summary"] is False
    assert captured["doc_type"] == "faq"
    assert captured["business_domain"] == "hr"
    assert captured["unified"]["doc_type"] == "faq"
    assert captured["unified"]["summary"] == ""
    assert result == {"doc_type": "faq", "business_domain": "hr"}


@pytest.mark.asyncio
async def test_failed_enrichment_does_not_trigger_second_summary_llm(monkeypatch):
    import backend.rag.preprocessing.metadata as metadata_module
    import backend.rag.preprocessing.metadata_llm as metadata_llm

    _patch_rule_fallback(monkeypatch)
    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", AsyncMock(return_value=None))
    monkeypatch.setattr(
        metadata_module, "build_llm_summary",
        AsyncMock(side_effect=AssertionError("不应发起第二次摘要调用")),
        raising=False,
    )
    registry = SimpleNamespace(list_all=lambda: {})
    stage = MetadataStage(registry=registry, embedding=None)

    result = await stage.build(
        "这是一段较长的文档内容。" * 250,
        {"doc_id": "doc-2", "source_file": "report.md", "file_path": "report.md"},
        chunks_text=[],
    )

    metadata_llm.extract_metadata_llm_async.assert_awaited_once()
    metadata_module.build_llm_summary.assert_not_awaited()
    assert result["doc_type"] == "general"
    assert result["business_domain"] == "hr"
    assert result["summary"] == "摘取式摘要"


@pytest.mark.asyncio
async def test_structured_faq_skips_metadata_and_question_generation_calls(monkeypatch):
    import backend.rag.preprocessing.metadata_llm as metadata_llm

    _patch_rule_fallback(monkeypatch, doc_type="faq")
    extract = AsyncMock(return_value={"doc_type": "general"})
    monkeypatch.setattr(metadata_llm, "extract_metadata_llm_async", extract)
    from backend.rag.preprocessing import question_gen
    generate_questions = Mock(
        side_effect=AssertionError("结构化 FAQ 不应调用模拟问题生成")
    )
    monkeypatch.setattr(
        question_gen, "generate_chunk_questions", generate_questions,
    )
    registry = SimpleNamespace(list_all=lambda: {})
    stage = MetadataStage(registry=registry, embedding=None)
    context = ProcessingRunContext.create("doc-3", "hash-3", "upload")
    recorder = ProcessingRunRecorder(context)
    body = "**Q:** 年假如何申请？\n**A:** 通过 OA 系统提交申请。\n" + "制度说明。" * 300

    result = await stage.build(
        body,
        {"doc_id": "doc-3", "source_file": "faq.docx", "file_path": "faq.docx"},
        chunks_text=["**Q:** 年假如何申请？\n**A:** 通过 OA 系统提交申请。"],
        processing_recorder=recorder,
    )

    extract.assert_not_awaited()
    generate_questions.assert_not_called()
    assert result["doc_type"] == "faq"
    assert result["summary"] == ""
    assert "complexity" not in result
    assert result["questions_by_chunk"] == []
    question_step = next(
        step for step in context.snapshot().steps
        if step.stage == "question_gen"
    )
    assert question_step.status == "skipped"
    assert question_step.skip_reason == "structured_faq"


@pytest.mark.asyncio
async def test_person_names_are_taken_from_merged_llm_entities(monkeypatch):
    import backend.config.rag as rag_config

    _patch_rule_fallback(monkeypatch, doc_type="policy")
    monkeypatch.setattr(rag_config, "ENABLE_SIMULATED_QUESTIONS", False)
    stage = MetadataStage(registry=SimpleNamespace(list_all=lambda: {}), embedding=None)
    monkeypatch.setattr(stage, "detect_near_dup", lambda *args, **kwargs: None)
    unified = {
        "entities": {"person": ["张三"], "org": ["示例科技有限公司"]},
        "keywords": [],
        "time_refs": [],
        "risk": {"level": "none", "signals": []},
        "llm_tokens": {},
        "provider_called": True,
    }

    result = await stage.finalize_unified(
        "张三在示例科技有限公司负责制度维护。",
        {"doc_id": "doc-person"},
        unified,
        rule_classification={
            "doc_type": "policy",
            "confidence": 0.91,
            "business_domain": "hr",
        },
    )

    assert result["entities"]["org"] == ["示例科技有限公司"]
    assert result["person_names"] == ["张三"]
