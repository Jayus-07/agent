"""上传重复判定和历史 Trace 关联的回归检查。"""

import time

from backend.app.api.routes import rag_upload


class _FakeChunkStore:
    def __init__(self, rows):
        self.rows = rows

    def get_by_doc_id(self, _doc_id):
        return self.rows


def test_incomplete_active_document_is_not_treated_as_duplicate(
    tmp_path, monkeypatch,
):
    source = tmp_path / "source.md"
    source.write_text("已恢复的原文", encoding="utf-8")
    expected_hash = rag_upload.sha256_of_file(str(source))
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store",
        lambda: _FakeChunkStore([]),
    )

    complete = rag_upload._active_document_artifacts_complete(
        {"doc_id": "doc-1", "chunk_count": 2}, str(source), expected_hash,
    )

    assert complete is False


def test_complete_active_document_requires_matching_source_hash(
    tmp_path, monkeypatch,
):
    source = tmp_path / "source.md"
    source.write_text("原文", encoding="utf-8")
    expected_hash = rag_upload.sha256_of_file(str(source))
    monkeypatch.setattr(
        "backend.rag.indexing.chunk_store.get_chunk_store",
        lambda: _FakeChunkStore([{}, {}]),
    )

    complete = rag_upload._active_document_artifacts_complete(
        {"doc_id": "doc-1", "chunk_count": 2}, str(source), expected_hash,
    )
    wrong_source = rag_upload._active_document_artifacts_complete(
        {"doc_id": "doc-1", "chunk_count": 2}, str(source), "wrong-hash",
    )

    assert complete is True
    assert wrong_source is False


def test_latest_document_trace_is_attached_to_duplicate_event(
    monkeypatch,
):
    events = []
    operation_logs = []
    monkeypatch.setattr(
        rag_upload, "_safe_log_op",
        lambda *args, **kwargs: operation_logs.append((args, kwargs)),
    )

    rag_upload._settle_index_result(
        upload_id="upload-1",
        filepath="/docs/kb/general/source.md",
        filename="source.md",
        source="manual",
        batch_id=None,
        kb_id="kb",
        upload_elapsed_ms=20,
        was_overwrite=False,
        upload_t0=time.time(),
        result={
            "terminal": "duplicate",
            "doc": {"doc_id": "doc-1", "chunk_count": 2},
            "trace_id": "trace-1",
            "processing_run_id": "run-1",
            "trace_association": "previous_document_processing",
        },
        emit_fn=lambda *args, **kwargs: events.append((args, kwargs)),
    )

    assert events[0][1]["trace_id"] == "trace-1"
    assert events[0][1]["processing_run_id"] == "run-1"
    assert events[0][1]["trace_association"] == "previous_document_processing"
    assert operation_logs[0][1]["trace_id"] == "trace-1"
