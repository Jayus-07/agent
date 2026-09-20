"""索引器把上传处理各阶段写入运行血缘的集成契约。"""

from unittest.mock import MagicMock

from backend.rag.indexing.indexer import IncrementalIndexer
from backend.shared.processing_context import get_processing_binding


class _FakeLineageRepository:
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


def test_indexer_records_upload_model_lineage(tmp_path, monkeypatch):
    document = tmp_path / "lineage.txt"
    document.write_text("这是足够长的测试文档内容。" * 20, encoding="utf-8")

    vectordb = MagicMock()
    vector_bindings = []

    def _add_documents(*args, **kwargs):
        vector_bindings.append(get_processing_binding())
        return ["chunk-1"]

    vectordb.add_documents.side_effect = _add_documents
    vectordb._collection_name = "test_chunks"
    doc_db = MagicMock()
    doc_db.add_texts.return_value = ["doc-1"]
    embedding = MagicMock()
    embedding.embed_documents.side_effect = lambda texts: [[0.1] * 3 for _ in texts]
    embedding.model_name = "bge-test"
    embedding._provider = "local"

    registry = MagicMock()
    registry.list_all.return_value = {}
    registry.get_by_path.return_value = None

    async def _metadata_stub(self, full_text, base_meta, parent_span_id="", chunks_text=None):
        return dict(base_meta)

    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", _metadata_stub)
    repository = _FakeLineageRepository()
    indexer = IncrementalIndexer(
        str(tmp_path), vectordb, doc_db, embedding, registry,
        processing_lineage_repository=repository,
        processing_task_id="upload-1",
        processing_batch_id="batch-1",
    )

    result = indexer._index_file(str(document))

    assert result["status"] == "active"
    assert repository.runs[0].task_id == "upload-1"
    assert repository.runs[0].batch_id == "batch-1"
    assert repository.finished[0][1] == "success"
    stages = {step.stage: step for step in repository.steps}
    assert {"load", "parser", "ocr", "semantic_chunk", "metadata", "table_describe",
            "embedding", "vector_write"} <= set(stages)
    assert stages["embedding"].model_name == "bge-test"
    assert vector_bindings[0] is not None
    assert vector_bindings[0].stage == "vector_write"
