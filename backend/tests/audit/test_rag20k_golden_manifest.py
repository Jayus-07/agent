from __future__ import annotations

import json
from pathlib import Path

from backend.audit.rag20k.golden_manifest import (
    EXPECTED_CATEGORY_COUNTS,
    validate_golden_manifest,
)

_FIXTURES = Path(__file__).parent / "fixtures"
VALID_FIXTURE = _FIXTURES / "rag20k_golden_valid.jsonl"


def fixture_path(name: str) -> Path:
    return _FIXTURES / name


def _rewrite_reviewers(source: Path, target: Path, single_review_ratio: float) -> Path:
    """把大部分行改成单审，构造双审比例不足的失败夹具。"""
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    for index, row in enumerate(rows):
        if index / len(rows) < single_review_ratio:
            row["reviewers"] = [row["reviewers"][0]]
    target.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return target


def test_category_distribution_is_exact() -> None:
    result = validate_golden_manifest(VALID_FIXTURE)

    assert result.category_counts == EXPECTED_CATEGORY_COUNTS
    assert result.valid is True


def test_second_review_covers_at_least_twenty_percent(tmp_path: Path) -> None:
    under_reviewed = _rewrite_reviewers(
        VALID_FIXTURE, tmp_path / "under_reviewed.jsonl", single_review_ratio=0.95
    )

    result = validate_golden_manifest(under_reviewed)

    assert result.valid is False
    assert result.second_review_ratio < 0.20


def test_reject_rows_must_not_expect_documents(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in VALID_FIXTURE.read_text(encoding="utf-8").splitlines()]
    rows[0]["should_reject"] = True  # FAQ 类允许拒答即互斥被破坏
    path = tmp_path / "bad_exclusivity.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    result = validate_golden_manifest(path)

    assert result.valid is False
    assert result.error_line_count == 1


def test_duplicate_case_ids_are_flagged(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in VALID_FIXTURE.read_text(encoding="utf-8").splitlines()]
    rows[1]["case_id"] = rows[0]["case_id"]
    path = tmp_path / "dup.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    result = validate_golden_manifest(path)

    assert result.valid is False
    assert result.duplicate_case_id_count == 1


def test_disputed_annotation_blocks_phase0(tmp_path: Path) -> None:
    """分歧未裁决的标注不得混入黄金集。"""
    rows = [json.loads(line) for line in VALID_FIXTURE.read_text(encoding="utf-8").splitlines()]
    rows[0]["annotation_status"] = "disputed"
    path = tmp_path / "disputed.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    result = validate_golden_manifest(path)

    assert result.valid is False
    assert result.error_line_count == 1


def test_summary_contains_only_aggregates() -> None:
    result = validate_golden_manifest(VALID_FIXTURE)
    summary = json.dumps(result.to_summary_dict(), ensure_ascii=False)

    assert "夹具问题" not in summary
    assert result.row_count == 500
    assert result.second_review_ratio >= 0.20


def test_cli_blocks_when_real_annotations_missing(tmp_path: Path, capsys) -> None:
    from backend.scripts.validate_rag20k_golden import run

    output = tmp_path / "validation-summary.json"
    exit_code = run(
        [
            "D:/rag20k/manifests/golden-500.jsonl",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 2
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["status"] == "blocked"
    assert "golden-500" in summary["blocking_reason"][0]
