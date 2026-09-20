"""四类文件的入库处理血缘端到端契约。

测试只替换外部解析器、Embedding、向量库和元数据模型边界，真实运行
IncrementalIndexer 的阶段收口、Registry 指针和血缘模型，不发起网络调用。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode


class _LineageRepository:
    def __init__(self):
        self.runs = []
        self.steps = []
        self.finished = []

    def create_run(self, snapshot):
        self.runs.append(snapshot)

    def upsert_stage(self, snapshot):
        self.steps.append(snapshot)

    def finish_run(self, run_id, status, **kwargs):
        self.finished.append((run_id, status, kwargs))


class _Registry:
    def __init__(self):
        self.rows = {}

    def get_by_path(self, path):
        return self.rows.get(path)

    def register_in_progress(self, file_path, **kwargs):
        self.rows[file_path] = {
            **kwargs,
            "file_path": file_path,
            "status": "parsing",
        }

    def register(self, *, file_path, doc_id, file_hash, kb_id, chunk_ids,
                 doc_db_id, metadata):
        self.rows[file_path] = {
            **metadata,
            "file_path": file_path,
            "doc_id": doc_id,
            "file_hash": file_hash,
            "kb_id": kb_id,
            "chunk_ids": json.dumps(chunk_ids),
            "doc_db_id": doc_db_id,
            "status": "active",
            "chunk_count": len(chunk_ids),
        }

    def update_fields(self, file_path, fields):
        self.rows.setdefault(file_path, {}).update(fields)


def _raw_ast(
    *,
    tabular: bool,
    ocr: bool,
    ocr_required: bool | None = None,
    ocr_attempted: bool | None = None,
    ocr_pages: int | None = None,
) -> DocumentAST:
    if tabular:
        node = DocumentNode(
            "table",
            "科目 金额\n货币资金 123456",
            rows=[["科目", "金额"], ["货币资金", "123456"]],
        )
        raw_text = "科目,金额\n货币资金,123456"
    else:
        text = "这是一段用于四格式入库血缘测试的正文，包含足够的结构和内容。"
        node = DocumentNode("paragraph", text * 3)
        raw_text = node.text
    return DocumentAST(
        root=DocumentNode("section", "", children=[node]),
        raw_text=raw_text,
        ocr_required=ocr if ocr_required is None else ocr_required,
        ocr_attempted=ocr if ocr_attempted is None else ocr_attempted,
        ocr_triggered=ocr,
        ocr_pages=(2 if ocr else 0) if ocr_pages is None else ocr_pages,
    )


@pytest.mark.parametrize(
    (
        "filename", "tabular", "ocr", "table_status", "ocr_required",
        "ocr_attempted", "ocr_status", "ocr_pages", "ocr_outcome",
    ),
    [
        ("text.pdf", False, False, "skipped", False, False, "skipped", 0, ""),
        ("scan.pdf", False, True, "skipped", True, True, "success", 2, ""),
        (
            "scan-disabled.pdf", False, False, "skipped", True, False,
            "skipped", 0, "",
        ),
        (
            "scan-cached.pdf", False, True, "skipped", True, True,
            "cached", 2, "cached",
        ),
        (
            "scan-partial.pdf", False, True, "skipped", True, True,
            "fallback", 1, "partial",
        ),
        ("policy.docx", False, False, "skipped", False, False, "skipped", 0, ""),
        ("finance.xlsx", True, False, "success", False, False, "skipped", 0, ""),
        ("finance.csv", True, False, "success", False, False, "skipped", 0, ""),
    ],
)
def test_four_format_indexing_records_stage_matrix(
    tmp_path, monkeypatch, filename, tabular, ocr, table_status,
    ocr_required, ocr_attempted, ocr_status, ocr_pages, ocr_outcome,
):
    """不同文件格式必须产生正确的 OCR/表格阶段和成功 current pointer。"""
    document = tmp_path / filename
    document.write_bytes(b"fixture-content-for-lineage")

    raw_ast = _raw_ast(
        tabular=tabular,
        ocr=ocr,
        ocr_required=ocr_required,
        ocr_attempted=ocr_attempted,
        ocr_pages=ocr_pages,
    )
    chunks = [
        Document(
            page_content="科目 货币资金 金额 123456" if tabular else "正文内容" * 20,
            metadata={"chunk_type": "table_row"} if tabular else {},
        )
    ]

    def _parse_stub(path):
        if ocr_outcome == "cached":
            from backend.rag.preprocessing.parser import ocr as ocr_module
            ocr_module._record_ocr_result(status="cached", cache_status="hit")
            ocr_module._record_ocr_result(status="cached", cache_status="hit")
        elif ocr_outcome == "partial":
            from backend.rag.preprocessing.parser import ocr as ocr_module
            ocr_module._record_ocr_result(status="success", cache_status="miss")
            ocr_module._record_ocr_result(status="failed", cache_status="miss")
        return list(chunks), {
            "raw_ast": raw_ast,
            "normalized_ast": raw_ast,
            "completeness": None,
            "doc_type": "general",
            "strategy_name": "fixture",
        }

    from backend.rag.preprocessing import pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "parse_and_chunk_full", _parse_stub)

    # 让质量门禁只验证当前 fixture 的 AST，不产生真实质量文件。
    from backend.rag.preprocessing import quality_gate
    monkeypatch.setattr(
        quality_gate,
        "persist_quality_record",
        lambda record: str(tmp_path / "quality.json"),
    )

    async def _metadata_stub(self, full_text, base_meta, parent_span_id="",
                             chunks_text=None, processing_recorder=None):
        if processing_recorder is not None:
            for stage, role in (
                ("metadata_extract", "metadata_extract"),
                ("question_gen", "question_gen"),
            ):
                token = processing_recorder.begin_stage(
                    stage, role=role, engine_type="rule",
                )
                processing_recorder.finish_stage(
                    token[0], status="skipped", started_at=token[1],
                    skip_reason="fixture_external_model_disabled",
                )
        return {
            **base_meta,
            "doc_type": "general",
            "business_domain": "general",
            "summary": "",
            "questions_by_chunk": [[] for _ in (chunks_text or [])],
            "llm_used": False,
        }

    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", _metadata_stub)

    # 表格描述只替换模型边界，验证真实 table_describe stage 收口。
    from backend.rag.preprocessing import table_describe
    monkeypatch.setattr(table_describe, "ENABLE_TABLE_DESCRIPTIONS", True)
    monkeypatch.setattr(table_describe, "_cache_get", lambda key: None)
    monkeypatch.setattr(table_describe, "_cache_put", lambda key, value: None)
    fake_response = MagicMock()
    fake_response.content = json.dumps({"descriptions": ["该行记录资金金额。"]})
    monkeypatch.setattr(table_describe, "_invoke_llm", lambda *args, **kwargs: fake_response)

    from backend.config import rag as rag_config
    monkeypatch.setattr(rag_config, "RAG_OCR_PROVIDER", "rapidocr")

    vector_db = MagicMock()
    vector_db._collection_name = "fixture_chunks"
    vector_db.add_documents.return_value = ["chunk-1"]
    doc_db = MagicMock()
    doc_db.add_texts.return_value = ["doc-1"]
    embedding = MagicMock()
    embedding.model_name = "bge-e2e"
    embedding._provider = "local"
    embedding.embed_documents.side_effect = lambda texts: [[0.1] * 3 for _ in texts]
    registry = _Registry()
    repository = _LineageRepository()

    indexer = IncrementalIndexer(
        str(tmp_path), vector_db, doc_db, embedding, registry,
        processing_lineage_repository=repository,
        processing_task_id=f"task-{filename}",
        processing_batch_id="batch-e2e",
    )
    result = indexer._index_file(str(document))

    assert result["status"] == "active"
    assert repository.finished[0][1] == "success"
    run_id = repository.runs[0].run_id
    assert registry.rows[str(document)]["last_processing_run_id"] == run_id
    assert registry.rows[str(document)]["processing_status"] == "success"
    assert registry.rows[str(document)]["ocr_used"] is (ocr_status != "skipped")
    assert registry.rows[str(document)]["ocr_model"] == (
        "RapidOCR" if ocr_attempted else ""
    )

    stages = {step.stage: step for step in repository.steps}
    assert {
        "load", "parser", "ocr", "semantic_chunk", "metadata",
        "metadata_extract", "question_gen", "table_describe",
        "embedding", "vector_write",
    } <= set(stages)
    assert stages["ocr"].status == ocr_status
    if ocr_status == "skipped":
        expected_skip_reason = "not_pdf"
        if document.suffix.lower() == ".pdf":
            expected_skip_reason = (
                "feature_disabled" if ocr_required else "text_layer_sufficient"
            )
        assert stages["ocr"].skip_reason == expected_skip_reason
    elif ocr_status == "fallback":
        assert stages["ocr"].fallback_reason == "ocr_page_failed"
    assert stages["table_describe"].status == table_status
    if table_status == "skipped":
        assert stages["table_describe"].model_name is None
    else:
        assert stages["table_describe"].model_name
    if ocr:
        assert stages["ocr"].model_name == "RapidOCR"
    assert stages["embedding"].model_name == "bge-e2e"
    assert stages["metadata_extract"].skip_reason == "fixture_external_model_disabled"
