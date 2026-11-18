"""验证文档摘要向量归属到对应的处理运行。"""

from backend.rag.indexing import indexer as indexer_module
from backend.rag.indexing.indexer import _add_doc_level_vector_with_lineage
from backend.rag.indexing.processing_lineage import (
    ModelIdentity,
    ProcessingRunContext,
)
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder
from backend.shared.processing_context import get_processing_binding


def test_doc_summary_embedding_is_bound_to_processing_run(monkeypatch):
    run = ProcessingRunContext.create(
        doc_id="doc-test",
        file_hash="sha-test",
        operation="reindex",
        trace_id="trace-test",
    )
    recorder = ProcessingRunRecorder(run)
    seen_bindings = []

    class DocStore:
        def add_texts(self, *, texts, metadatas):
            binding = get_processing_binding()
            seen_bindings.append(binding)
            assert texts == ["test summary"]
            assert len(metadatas) == 1
            return ["summary-vector"]

    monkeypatch.setattr(
        indexer_module,
        "_embedding_model_identity",
        lambda _embedding: ModelIdentity(
            role="embedding",
            engine_type="embedding",
            provider="custom-gpu",
            model_name="bge-m3",
        ),
    )

    ids = _add_doc_level_vector_with_lineage(
        DocStore(),
        "test summary",
        {"doc_id": "doc-test"},
        lineage_recorder=recorder,
        embedding=object(),
    )

    assert ids == ["summary-vector"]
    assert len(seen_bindings) == 1
    assert seen_bindings[0].run_id == run.run_id
    assert seen_bindings[0].step_id
    assert seen_bindings[0].role == "embedding"
    assert seen_bindings[0].stage == "embedding"

    step = run.snapshot().steps[0]
    assert step.status == "success"
    assert step.provider == "custom-gpu"
    assert step.model_name == "bge-m3"
    assert step.input_count == 1
    assert step.output_count == 1
    assert step.metadata["index_scope"] == "document_summary"
