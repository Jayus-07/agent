"""RAG 20k 语料清单（JSONL）的流式校验。

逐行读取、不把 2 万行全部载入内存；绝不打开 `object_uri` 指向的文档正文。
摘要只包含数量、分布、大小分位数、复合键的不可逆哈希与错误行号，
不包含 doc_id、object_uri 或任何可逆键。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# 格式白名单来自审计报告 Q5 的暂定分布；PPT/PPTX/HTML 与未知扩展名一律报错。
ALLOWED_FORMATS = frozenset({"pdf", "docx", "xlsx", "csv", "md", "txt"})

_REQUIRED_STR_FIELDS = (
    "tenant_id",
    "kb_id",
    "doc_id",
    "permission_scope",
    "language",
    "authorization_ref",
)
_COMPOUND_KEY_FIELDS = ("tenant_id", "kb_id", "doc_id", "version")
_MAX_LISTED_ERROR_LINES = 200
_MAX_DUPLICATE_SAMPLES = 50
_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True)
class CorpusValidationResult:
    """语料清单校验结果；字段名即对外摘要契约。"""

    valid: bool
    expected_count: int
    row_count: int
    duplicate_key_count: int
    error_line_count: int
    error_lines: list[int]
    uri_scheme_counts: dict[str, int]
    format_counts: dict[str, int]
    permission_scope_counts: dict[str, int]
    language_counts: dict[str, int]
    ocr_count: int
    size_bytes_percentiles: dict[str, int]
    duplicate_key_sample: list[str]

    def to_summary_dict(self, *, input_path: str | None = None) -> dict[str, object]:
        summary: dict[str, object] = {
            "schema_version": "1.0",
            "status": "ok" if self.valid else "failed",
            "valid": self.valid,
            "expected_count": self.expected_count,
            "row_count": self.row_count,
            "duplicate_key_count": self.duplicate_key_count,
            "error_line_count": self.error_line_count,
            "error_lines": self.error_lines[:_MAX_LISTED_ERROR_LINES],
            "uri_scheme_counts": dict(self.uri_scheme_counts),
            "format_counts": dict(self.format_counts),
            "permission_scope_counts": dict(self.permission_scope_counts),
            "language_counts": dict(self.language_counts),
            "ocr_count": self.ocr_count,
            "size_bytes_percentiles": dict(self.size_bytes_percentiles),
            "duplicate_key_sample": list(self.duplicate_key_sample),
        }
        if input_path is not None:
            summary["input_path"] = input_path
        return summary


def _hash_compound_key(row: dict[str, object]) -> str:
    raw = "\0".join(str(row[field_name]) for field_name in _COMPOUND_KEY_FIELDS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row_errors(row: object) -> list[str]:
    if not isinstance(row, dict):
        return ["行不是 JSON 对象"]

    errors: list[str] = []
    for field_name in _REQUIRED_STR_FIELDS:
        value = row.get(field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} 缺失或为空")

    version = row.get("version")
    if isinstance(version, bool) or not (
        (isinstance(version, str) and version.strip())
        or (isinstance(version, int) and not isinstance(version, bool))
    ):
        errors.append("version 缺失或为空")

    size_bytes = row.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        errors.append("size_bytes 必须是非负整数")

    if not isinstance(row.get("is_ocr"), bool):
        errors.append("is_ocr 必须是布尔值")

    content_sha256 = row.get("content_sha256")
    if (
        not isinstance(content_sha256, str)
        or len(content_sha256) != _SHA256_HEX_LENGTH
        or any(ch not in "0123456789abcdef" for ch in content_sha256)
    ):
        errors.append("content_sha256 必须是 64 位小写十六进制")

    format_name = row.get("format")
    if not isinstance(format_name, str) or not format_name.strip():
        errors.append("format 缺失或为空")
    elif format_name not in ALLOWED_FORMATS:
        errors.append(f"format 不在白名单: {format_name}")

    object_uri = row.get("object_uri")
    if not isinstance(object_uri, str) or "://" not in object_uri:
        errors.append("object_uri 缺少 scheme")
    else:
        scheme, _, rest = object_uri.partition("://")
        if not scheme.strip() or not rest.strip():
            errors.append("object_uri scheme 或路径为空")
    return errors


def _percentiles(sizes: list[int]) -> dict[str, int]:
    if not sizes:
        return {"p50": 0, "p95": 0, "p99": 0}
    ordered = sorted(sizes)

    def _at(quantile: float) -> int:
        rank = math.ceil(quantile * len(ordered))  # 1-based rank
        return ordered[max(0, rank - 1)]

    return {"p50": _at(0.50), "p95": _at(0.95), "p99": _at(0.99)}


def validate_corpus_manifest(
    path: Path,
    expected_count: int = 20_000,
) -> CorpusValidationResult:
    """流式校验语料清单；数量、错误行、重复键任一不满足即 valid=False。"""

    seen_keys: set[str] = set()
    duplicate_hashes: list[str] = []
    format_counts: Counter[str] = Counter()
    scheme_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    sizes: list[int] = []
    error_lines: list[int] = []
    ocr_count = 0
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
            format_name = row.get("format")
            if isinstance(format_name, str) and format_name.strip():
                format_counts[format_name] += 1

            errors = _row_errors(row)
            if errors:
                error_lines.append(line_number)
                continue

            scope_counts[str(row["permission_scope"])] += 1
            language_counts[str(row["language"])] += 1
            sizes.append(int(row["size_bytes"]))  # type: ignore[arg-type]
            if row["is_ocr"]:
                ocr_count += 1
            scheme = str(row["object_uri"]).partition("://")[0]
            scheme_counts[scheme] += 1

            key_hash = _hash_compound_key(row)
            raw_key = "\0".join(str(row[field_name]) for field_name in _COMPOUND_KEY_FIELDS)
            if raw_key in seen_keys and key_hash not in duplicate_hashes:
                duplicate_hashes.append(key_hash)
            seen_keys.add(raw_key)

    duplicate_key_count = len(duplicate_hashes)
    return CorpusValidationResult(
        valid=(
            row_count == expected_count
            and not error_lines
            and duplicate_key_count == 0
        ),
        expected_count=expected_count,
        row_count=row_count,
        duplicate_key_count=duplicate_key_count,
        error_line_count=len(error_lines),
        error_lines=error_lines,
        uri_scheme_counts=dict(scheme_counts),
        format_counts=dict(format_counts),
        permission_scope_counts=dict(scope_counts),
        language_counts=dict(language_counts),
        ocr_count=ocr_count,
        size_bytes_percentiles=_percentiles(sizes),
        duplicate_key_sample=duplicate_hashes[:_MAX_DUPLICATE_SAMPLES],
    )
