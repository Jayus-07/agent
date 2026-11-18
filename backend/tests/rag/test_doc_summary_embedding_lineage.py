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
    class Repository:
        def __init__(self):
            self.steps = []

        def create_run(self, _snapshot):
            pass

        def upsert_stage(self, snapshot):
            self.steps.append(snapshot)

    repository = Repository()
    recorder = ProcessingRunRecorder(run, repository)
    recorder.start()
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

    chunk_step, chunk_started_at = recorder.begin_stage(
        "embedding",
        role="embedding",
        engine_type="embedding",
        model=ModelIdentity(
            role="embedding",
            engine_type="embedding",
            provider="custom-gpu",
            model_name="bge-m3",
        ),
    )
    recorder.finish_stage(
        chunk_step,
        status="success",
        started_at=chunk_started_at,
        input_count=2,
        output_count=2,
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
    assert seen_bindings[0].stage == "document_summary_embedding"

    steps = run.snapshot().steps
    assert {(step.stage, step.attempt_no) for step in repository.steps} == {
        ("embedding", 1),
        ("document_summary_embedding", 1),
    }
    step = next(step for step in steps if step.stage == "document_summary_embedding")
    assert step.status == "success"
    assert step.provider == "custom-gpu"
    assert step.model_name == "bge-m3"
    assert step.input_count == 1
    assert step.output_count == 1
    assert step.metadata["index_scope"] == "document_summary"
