from __future__ import annotations

import json
from pathlib import Path

from backend.audit.rag20k.corpus_manifest import (
    validate_corpus_manifest,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _valid_row(**overrides: object) -> dict:
    row = {
        "tenant_id": "tenant-a",
        "kb_id": "kb-main",
        "doc_id": "doc-000001",
        "version": "v1",
        "format": "pdf",
        "size_bytes": 2048,
        "permission_scope": "tenant",
        "language": "zh",
        "is_ocr": False,
        "content_sha256": "a" * 64,
        "object_uri": "s3://private-bucket/docs/doc-000001.pdf",
        "authorization_ref": "auth/agreement-2026-001",
    }
    row.update(overrides)
    return row


def write_jsonl(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_manifest_rejects_duplicate_compound_key(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path,
        [_valid_row(doc_id="same"), _valid_row(doc_id="same", size_bytes=4096)],
    )

    result = validate_corpus_manifest(path, expected_count=2)

    assert result.valid is False
    assert result.duplicate_key_count == 1


def test_manifest_never_reads_document_body(tmp_path: Path) -> None:
    """校验器只统计 URI scheme，绝不打开对象存储或读取文档正文。"""
    path = write_jsonl(tmp_path, [_valid_row(object_uri="s3://private/doc.pdf")])

    result = validate_corpus_manifest(path, expected_count=1)

    assert result.valid is True
    assert result.uri_scheme_counts == {"s3": 1}


def test_missing_required_fields_report_error_lines(tmp_path: Path) -> None:
    rows = [
        _valid_row(doc_id="doc-a"),
        {k: v for k, v in _valid_row(doc_id="doc-b").items() if k != "permission_scope"},
        _valid_row(doc_id="doc-c", content_sha256="short"),
    ]
    path = write_jsonl(tmp_path, rows)

    result = validate_corpus_manifest(path, expected_count=3)

    assert result.valid is False
    assert result.error_line_count == 2
    assert result.error_lines == [2, 3]


def test_row_count_mismatch_fails_validation(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path, [_valid_row()])

    result = validate_corpus_manifest(path, expected_count=2)

    assert result.valid is False
    assert result.row_count == 1
    assert result.expected_count == 2


def test_forbidden_or_unknown_formats_are_flagged(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path, [_valid_row(doc_id="doc-a", format="pptx")])

    result = validate_corpus_manifest(path, expected_count=1)

    assert result.valid is False
    assert result.format_counts == {"pptx": 1}
    assert result.error_line_count == 1


def test_summary_keeps_only_sanitized_aggregates(tmp_path: Path) -> None:
    """摘要只允许分布与统计：不得出现 doc_id、object_uri 或可逆的复合键。"""
    rows = [
        _valid_row(doc_id="doc-a", size_bytes=1000),
        _valid_row(doc_id="doc-a", size_bytes=2000, is_ocr=True),
        _valid_row(doc_id="doc-b", size_bytes=3000, format="md", language="en"),
    ]
    path = write_jsonl(tmp_path, rows)

    result = validate_corpus_manifest(path, expected_count=3)
    summary = json.dumps(result.to_summary_dict(), ensure_ascii=False)

    assert "doc-a" not in summary and "s3://private" not in summary
    assert len(result.duplicate_key_sample) == 1
    assert all(len(key) == 64 for key in result.duplicate_key_sample)
    assert result.size_bytes_percentiles["p50"] == 2000
    assert result.ocr_count == 1


def test_malformed_json_line_is_counted_as_error(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        json.dumps(_valid_row(), ensure_ascii=False) + "\n" + "{broken\n",
        encoding="utf-8",
    )

    result = validate_corpus_manifest(path, expected_count=2)

    assert result.valid is False
    assert result.error_lines == [2]
    assert result.row_count == 1


def test_valid_fixture_passes() -> None:
    result = validate_corpus_manifest(
        _FIXTURES / "rag20k_corpus_valid.jsonl", expected_count=3
    )

    assert result.valid is True
    assert result.duplicate_key_count == 0
