"""PR #55 覆盖审计进入既有 QualityGate 的回归测试。"""

import pytest

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.quality_gate import (
    anomaly_summary,
    build_quality_record,
    hard_anomalies,
    run_typed_validation,
)


def _ast() -> DocumentAST:
    text = "本段正文未进入实际输出的 Leaf。"
    return DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=text),
        ]),
        raw_text=text,
    )


def _record(tmp_path, coverage: dict) -> dict:
    from langchain_core.documents import Document

    return build_quality_record(
        file_path=str(tmp_path / "coverage.md"), doc_id="d-coverage",
        kb_id="kb-test", raw_ast=_ast(),
        chunks=[Document(
            page_content="本段正文未进入实际输出的 Leaf。",
            metadata={"granularity": "leaf"},
        )],
        file_size=32, file_hash="sha256", coverage=coverage,
    )


def test_coverage_gap_is_recorded_as_hard_quality_issue(tmp_path):
    report = {
        "leaf_total": 2,
        "audited_leaf_count": 2,
        "covered": 1,
        "uncovered_count": 1,
        "uncovered_samples": [{"type": "paragraph", "preview": "缺失段落"}],
        "sampled": False,
        "coverage_rate": 0.5,
    }

    record = _record(tmp_path, report)
    anomalies = run_typed_validation(record, _ast())
    hard = hard_anomalies(anomalies)

    assert record["coverage"] == report
    assert any(a["check"] == "coverage_gap" for a in hard)
    assert "coverage_gap" in anomaly_summary(anomalies, include_hard=True)


def test_coverage_audit_failure_is_not_treated_as_pass(tmp_path):
    record = _record(tmp_path, {"error": "RuntimeError: audit unavailable"})

    anomalies = run_typed_validation(record, _ast())

    assert any(
        a["check"] == "coverage_audit_failed" and a["severity"] == "error"
        for a in anomalies
    )


def test_sampled_coverage_is_labeled_in_quality_record(tmp_path):
    report = {
        "leaf_total": 2_500,
        "audited_leaf_count": 1_000,
        "covered": 1_000,
        "uncovered_count": 0,
        "uncovered_samples": [],
        "sampled": True,
        "coverage_rate": 1.0,  # 即使调用方错误填入 100%，Quality Record 也清除。
    }

    record = _record(tmp_path, report)
    anomalies = run_typed_validation(record, _ast())

    assert record["coverage"]["sampled"] is True
    assert record["coverage"]["coverage_rate"] is None
    assert any(a["check"] == "coverage_audit_sampled" for a in anomalies)


def test_malformed_coverage_report_is_a_hard_audit_failure(tmp_path):
    report = {
        "leaf_total": "invalid",
        "audited_leaf_count": 1,
        "uncovered_count": 0,
        "sampled": False,
    }

    anomalies = run_typed_validation(_record(tmp_path, report), _ast())

    assert any(
        item["check"] == "coverage_audit_failed"
        and item["severity"] == "error"
        for item in anomalies
    )


@pytest.mark.parametrize(
    ("initial_status", "expected_status"),
    [
        ("parsing", "failed"),
        ("active", "active"),
        ("pending_review", "pending_review"),
    ],
)
def test_index_failure_settles_only_current_placeholder(
    tmp_path, monkeypatch, initial_status, expected_status,
):
    """现有上传失败收口必须把 parsing 占位行落为 failed。"""
    from backend.app.api.routes import rag_upload

    class Registry:
        def __init__(self, status):
            self.status = status

        def get_by_path(self, _path):
            return {"status": self.status}

        def update_status(self, _path, status):
            self.status = status

    registry = Registry(initial_status)
    monkeypatch.setattr(rag_upload, "_get_registry", lambda: registry)
    monkeypatch.setattr(rag_upload, "_cleanup_candidate_artifacts", lambda *a: None)
    monkeypatch.setattr(rag_upload, "_safe_log_op", lambda *a, **k: None)

    rag_upload._settle_index_result(
        "upload-1", str(tmp_path / "coverage.md"), "coverage.md", "test",
        None, "kb-test", None, False, 0.0, result=None,
        emit_fn=lambda *args, **kwargs: None,
        exc=ValueError("质量门禁硬异常: coverage_gap"),
    )

    assert registry.status == expected_status


@pytest.mark.parametrize("candidate_mode", [False, True], ids=["normal", "candidate"])
def test_indexer_records_coverage_gap_and_trace_then_rejects_active(
    tmp_path, monkeypatch, candidate_mode,
):
    """真实索引质量段应持久化缺口、registry 质量字段与 Trace 后失败。"""
    from types import SimpleNamespace

    from langchain_core.documents import Document

    import backend.rag.indexing.indexer as indexer_module
    import backend.rag.preprocessing.pipeline as pipeline_module
    import backend.rag.preprocessing.quality_gate as quality_gate
    from backend.rag.indexing.indexer import IncrementalIndexer

    covered_text = "已有检索 Leaf 中的正文内容。" * 8
    missing_text = "质量门禁必须发现未覆盖的财务正文。" * 8
    source = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=covered_text),
            DocumentNode(type="paragraph", text=missing_text),
        ]),
        raw_text=covered_text + missing_text,
    )
    chunks = [Document(
        page_content=covered_text,
        metadata={"granularity": "leaf", "chunk_id": "leaf-0"},
    )]
    from backend.rag.preprocessing.chunking import coverage_audit

    coverage = coverage_audit(source, chunks)

    def parse_with_gap(_path):
        return chunks, {"raw_ast": source, "coverage": coverage}

    monkeypatch.setattr(pipeline_module, "parse_and_chunk_full", parse_with_gap)

    persisted = []
    monkeypatch.setattr(
        quality_gate, "persist_quality_record",
        lambda record: persisted.append(record) or "coverage-record.json",
    )

    class Registry:
        def __init__(self, status):
            self.status = status
            self.updates = []

        def get_by_path(self, _path):
            return {"status": self.status}

        def register_in_progress(self, *_args, **_kwargs):
            self.status = "parsing"

        def update_fields(self, _path, fields):
            self.updates.append(fields)

    class Collector:
        def __init__(self):
            self.spans = {}

        def start(self, **_kwargs):
            return SimpleNamespace(id="trace-coverage", tags={})

        def start_span(self, span_id, **kwargs):
            span = SimpleNamespace(
                span_id=span_id, input=kwargs.get("input"), metrics={},
                status="running", duration_ms=0,
            )
            self.spans[span_id] = span
            return span

        def end_span(self, span, status="success", metrics=None, **_kwargs):
            span.status = status
            span.metrics.update(metrics or {})

        def finish(self, *_args, **_kwargs):
            return None

    async def metadata_stub(self, _full_text, base_meta, **_kwargs):
        return dict(base_meta)

    registry = Registry("active" if candidate_mode else "parsing")
    collector = Collector()
    monkeypatch.setattr(indexer_module, "trace_collector", collector)
    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", metadata_stub)
    indexer = IncrementalIndexer(
        docs_dir=str(tmp_path), vectordb=SimpleNamespace(),
        doc_db=SimpleNamespace(), embedding=SimpleNamespace(), registry=registry,
        candidate_mode=candidate_mode,
    )
    document = tmp_path / "coverage.txt"
    document.write_text("测试覆盖缺口的索引内容。" * 5, encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="coverage_gap"):
        indexer._index_file(str(document))

    assert persisted
    assert persisted[0]["coverage"] == coverage
    assert "coverage_gap" in persisted[0]["quality_issues"]
    assert any(
        item.get("check") == "coverage_gap"
        and item.get("severity") == "error"
        for item in persisted[0]["validation"]["anomalies"]
    )
    if candidate_mode:
        # 候选失败不得改写同路径仍 active 的旧 registry 行。
        assert registry.updates == []
        assert registry.status == "active"
    else:
        assert registry.updates
        quality_fields = registry.updates[-1]
        assert "coverage_gap" in quality_fields["quality_issues"]
        assert quality_fields["quality_status"] == "failed"

    quality_span = collector.spans["index_quality"]
    assert quality_span.status == "error"
    assert quality_span.metrics["coverage_uncovered_count"] == 1
    assert quality_span.input["coverage"]["coverage_uncovered_count"] == 1
    assert quality_span.input["coverage"]["coverage_issue"] == "coverage_gap"
    assert "uncovered_samples" not in quality_span.input["coverage"]
