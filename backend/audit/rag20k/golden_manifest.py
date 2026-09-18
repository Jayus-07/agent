"""RAG 20k 阶段 0 黄金查询清单（JSONL）校验。

九类互斥分类固定配额（机器键 = 审计报告中文类名）：
``faq``=FAQ 100、``precise_lookup``=精确定位 80、``table``=表格 80、
``multi_condition``=多条件 70、``cross_doc``=跨文档 60、``permission``=权限 40、
``version``=版本 30、``no_evidence``=无依据 20、``ocr``=OCR 20，共 500 条。

互斥规则（可机器判定）：``should_reject=True`` 当且仅当期望文档/块为空；
回答类（faq/precise_lookup/table/multi_condition/cross_doc/ocr）必须给出期望
文档与块；权限、版本两类允许两种取向之一；无依据类必须拒答。分歧未裁决
（annotation_status 不在 annotated/reviewed/adjudicated 内）即整单失败。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TOTAL_EXPECTED = 500
SECOND_REVIEW_MIN_RATIO = 0.20

EXPECTED_CATEGORY_COUNTS: dict[str, int] = {
    "faq": 100,
    "precise_lookup": 80,
    "table": 80,
    "multi_condition": 70,
    "cross_doc": 60,
    "permission": 40,
    "version": 30,
    "no_evidence": 20,
    "ocr": 20,
}

_MUST_ANSWER_CATEGORIES = frozenset(
    {"faq", "precise_lookup", "table", "multi_condition", "cross_doc", "ocr"}
)
_DUAL_CATEGORIES = frozenset({"permission", "version"})
_MUST_REJECT_CATEGORIES = frozenset({"no_evidence"})
_ALLOWED_ANNOTATION_STATUSES = frozenset({"annotated", "reviewed", "adjudicated"})

_REQUIRED_STR_FIELDS = (
    "case_id",
    "category",
    "question",
    "tenant_id",
    "kb_id",
    "permission_context",
)
_MAX_LISTED_ERROR_LINES = 200


@dataclass(frozen=True)
class GoldenValidationResult:
    """黄金集校验结果；字段名即对外摘要契约。"""

    valid: bool
    row_count: int
    duplicate_case_id_count: int
    error_line_count: int
    error_lines: list[int]
    category_counts: dict[str, int]
    annotation_status_counts: dict[str, int]
    second_review_ratio: float

    def to_summary_dict(self, *, input_path: str | None = None) -> dict[str, object]:
        summary: dict[str, object] = {
            "schema_version": "1.0",
            "status": "ok" if self.valid else "failed",
            "valid": self.valid,
            "total_expected": TOTAL_EXPECTED,
            "row_count": self.row_count,
            "duplicate_case_id_count": self.duplicate_case_id_count,
            "error_line_count": self.error_line_count,
            "error_lines": self.error_lines[:_MAX_LISTED_ERROR_LINES],
            "category_counts": dict(self.category_counts),
            "annotation_status_counts": dict(self.annotation_status_counts),
            "second_review_ratio": round(self.second_review_ratio, 4),
            "second_review_min_ratio": SECOND_REVIEW_MIN_RATIO,
        }
        if input_path is not None:
            summary["input_path"] = input_path
        return summary


def _row_errors(row: object) -> list[str]:
    if not isinstance(row, dict):
        return ["行不是 JSON 对象"]

    errors: list[str] = []
    for field_name in _REQUIRED_STR_FIELDS:
        value = row.get(field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} 缺失或为空")

    category = row.get("category")
    if not isinstance(category, str) or category not in EXPECTED_CATEGORY_COUNTS:
        errors.append(f"category 不在九类之内: {category}")
        return errors

    should_reject = row.get("should_reject")
    if not isinstance(should_reject, bool):
        errors.append("should_reject 必须是布尔值")
        return errors

    expected_docs = row.get("expected_doc_ids")
    expected_chunks = row.get("expected_chunk_ids")
    for name, value in (("expected_doc_ids", expected_docs), ("expected_chunk_ids", expected_chunks)):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"{name} 必须是字符串数组")

    # 互斥判定：拒答 ⟺ 期望为空；两类取向不得同时出现或同时缺失。
    has_expectation = isinstance(expected_docs, list) and len(expected_docs) > 0
    if should_reject and has_expectation:
        errors.append("should_reject=true 时不得携带期望文档")
    if not should_reject and not has_expectation:
        errors.append("should_reject=false 时必须给出期望文档")

    if category in _MUST_ANSWER_CATEGORIES and should_reject:
        errors.append(f"回答类 {category} 不允许 should_reject=true")
    if category in _MUST_REJECT_CATEGORIES and not should_reject:
        errors.append(f"无依据类 {category} 必须 should_reject=true")
    # _DUAL_CATEGORIES 两类允许任一取向，只需满足上面的互斥判定。

    annotation_status = row.get("annotation_status")
    if not isinstance(annotation_status, str) or annotation_status not in _ALLOWED_ANNOTATION_STATUSES:
        errors.append(f"annotation_status 必须是 {'/'.join(sorted(_ALLOWED_ANNOTATION_STATUSES))}")

    reviewers = row.get("reviewers")
    if (
        not isinstance(reviewers, list)
        or not reviewers
        or not all(isinstance(item, str) and item.strip() for item in reviewers)
    ):
        errors.append("reviewers 必须是非空字符串数组")

    return errors


def validate_golden_manifest(path: Path) -> GoldenValidationResult:
    """流式校验黄金清单；配额、互斥、标注状态、双审比例任一不满足即失败。"""

    error_lines: list[int] = []
    category_counts: Counter[str] = Counter()
    annotation_counts: Counter[str] = Counter()
    seen_case_ids: set[str] = set()
    duplicate_case_ids: set[str] = set()
    second_review_rows = 0
    row_count = 0

    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                error_lines.append(line_number)
                continue
            if not isinstance(row, dict):
                error_lines.append(line_number)
                continue

            row_count += 1
            category = row.get("category")
            if isinstance(category, str) and category.strip():
                category_counts[category] += 1
            annotation_status = row.get("annotation_status")
            if isinstance(annotation_status, str) and annotation_status.strip():
                annotation_counts[annotation_status] += 1

            case_id = row.get("case_id")
            if isinstance(case_id, str) and case_id.strip():
                if case_id in seen_case_ids:
                    duplicate_case_ids.add(case_id)
                seen_case_ids.add(case_id)

            reviewers = row.get("reviewers")
            if (
                isinstance(reviewers, list)
                and len({item for item in reviewers if isinstance(item, str)}) >= 2
            ):
                second_review_rows += 1

            if _row_errors(row):
                error_lines.append(line_number)

    ratio = second_review_rows / row_count if row_count else 0.0
    return GoldenValidationResult(
        valid=(
            row_count == TOTAL_EXPECTED
            and not error_lines
            and not duplicate_case_ids
            and dict(category_counts) == EXPECTED_CATEGORY_COUNTS
            and ratio >= SECOND_REVIEW_MIN_RATIO
        ),
        row_count=row_count,
        duplicate_case_id_count=len(duplicate_case_ids),
        error_line_count=len(error_lines),
        error_lines=error_lines,
        category_counts=dict(category_counts),
        annotation_status_counts=dict(annotation_counts),
        second_review_ratio=ratio,
    )
