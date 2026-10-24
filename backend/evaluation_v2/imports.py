"""非 RAG V2 Golden 的受限 JSON/JSONL/CSV 预检。"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from backend.evaluation.models import MODULE_KINDS
from backend.evaluation_v2.domain_contracts import validate_non_rag_case

MAX_BYTES = 5 * 1024 * 1024
MAX_CASES = 100
_CASE_FIELDS = {
    "id", "module", "question", "input", "expected", "metadata", "tier",
    "query_type", "tags", "splits", "archived",
}
_MANIFEST_FIELDS = {
    "schema_version", "dataset_key", "name", "version", "module",
    "description", "language", "case_schema", "csv_mapping",
}


def validate_non_rag_upload(
    filename: str, data: bytes, manifest_data: Any,
) -> dict[str, Any]:
    """解析和校验候选文件；此路径只检查离线资产，不触发业务执行。"""
    errors: list[dict[str, Any]] = []
    try:
        manifest = _validate_manifest(manifest_data)
        text = data.decode("utf-8-sig")
        rows = _parse_rows(filename, text, manifest)
    except (UnicodeError, ValueError, TypeError, csv.Error) as exc:
        return {"valid": False, "case_count": 0, "errors": [{
            "line": 0, "field": "file/manifest", "message": str(exc)[:300],
        }], "cases": [], "manifest": manifest_data}

    if not 1 <= len(rows) <= MAX_CASES:
        errors.append({"line": 0, "field": "cases", "message": "每个版本需要 1–100 条用例"})
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for line, row in rows:
        try:
            case = _normalize_case(row)
            declared_module = row.get("module") if isinstance(row, dict) else None
            if declared_module is not None and declared_module != manifest["module"]:
                raise ValueError("case.module 必须与 manifest.module 一致")
            if case["id"] in seen:
                raise ValueError("用例 id 重复")
            seen.add(case["id"])
            validate_non_rag_case(
                manifest["module"],
                case["input"],
                case["expected"],
                case["expected"].get("evidence", []),
            )
            cases.append(case)
        except (TypeError, ValueError) as exc:
            errors.append({
                "line": line,
                "field": "expected",
                "message": str(exc)[:300],
            })

    return {
        "valid": not errors and len(cases) == len(rows),
        "case_count": len(cases),
        "errors": errors[:100],
        "cases": cases if not errors else [],
        "manifest": manifest,
    }


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("manifest 必须为 JSON 对象")
    if set(value) - _MANIFEST_FIELDS:
        raise ValueError("manifest 包含未支持的字段")
    module = str(value.get("module", "")).strip()
    if module not in MODULE_KINDS or module == "rag":
        raise ValueError("该导入路径仅适用于已登记的非 RAG 模块")
    if value.get("schema_version") != "evaluation-v2/1.0":
        raise ValueError("非 RAG manifest.schema_version 必须为 evaluation-v2/1.0")
    for field in ("dataset_key", "name", "version"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"manifest.{field} 必须是非空字符串")
    mapping = value.get("csv_mapping", {})
    if not isinstance(mapping, dict) or len(set(mapping.values())) != len(mapping):
        raise ValueError("csv_mapping 必须把标准字段映射到互不重复的来源列")
    if set(mapping) - _CASE_FIELDS or any(not isinstance(item, str) or not item for item in mapping.values()):
        raise ValueError("csv_mapping 含无效字段或空列名")
    return value


def _parse_rows(filename: str, text: str, manifest: dict[str, Any]) -> list[tuple[int, Any]]:
    lowered = filename.lower()
    if lowered.endswith(".jsonl"):
        rows: list[tuple[int, Any]] = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append((number, json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {number} 行 JSON 语法错误") from exc
        return rows
    if lowered.endswith(".json"):
        payload = json.loads(text)
        if not isinstance(payload, dict) or set(payload) != {"manifest", "cases"}:
            raise ValueError("JSON 顶层必须包含 manifest 和 cases")
        embedded_manifest = _validate_manifest(payload["manifest"])
        if embedded_manifest != manifest:
            raise ValueError("文件 manifest 与表单 manifest 不一致")
        if not isinstance(payload["cases"], list):
            raise ValueError("cases 必须为数组")
        return list(enumerate(payload["cases"], 1))
    if not lowered.endswith(".csv"):
        raise ValueError("仅支持 .jsonl、.json 和 .csv 文件")

    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV 必须有唯一列名")
    mapping = manifest.get("csv_mapping") or {}
    expected_columns = set(mapping.values()) if mapping else _CASE_FIELDS
    if set(reader.fieldnames) - expected_columns:
        raise ValueError("CSV 表头包含未映射列")
    rows = []
    for row in reader:
        if None in row:
            raise ValueError(f"CSV 第 {reader.line_num} 行列数超过表头")
        mapped = {key: row[column] for key, column in mapping.items()} if mapping else row
        parsed = {key: value for key, value in mapped.items() if value != ""}
        for field in ("input", "expected", "metadata", "tags", "splits", "archived"):
            if field in parsed:
                try:
                    parsed[field] = json.loads(parsed[field])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"CSV 第 {reader.line_num} 行 {field} JSON 语法错误") from exc
        rows.append((reader.line_num, parsed))
    return rows


def _normalize_case(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _CASE_FIELDS:
        raise ValueError("用例包含未支持的字段")
    case_id = value.get("id")
    question = value.get("question")
    case_input = value.get("input")
    expected = value.get("expected")
    metadata = value.get("metadata", {})
    if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 160:
        raise ValueError("用例 id 必须是 1–160 个字符")
    if question is not None and (
        not isinstance(question, str) or not question.strip() or len(question) > 12000
    ):
        raise ValueError("question 必须是 1–12000 个字符")
    if case_input is not None and (
        not isinstance(case_input, dict)
        or len(json.dumps(case_input, ensure_ascii=False).encode("utf-8")) > 64 * 1024
    ):
        raise ValueError("input 必须是 JSON 对象且不超过 64 KB")
    case_input = dict(case_input or {})
    query = case_input.get("query")
    if question is None:
        if isinstance(query, str) and query.strip():
            question = query.strip()
        else:
            user_turns = [
                turn for turn in case_input.get("turns", [])
                if isinstance(turn, dict) and turn.get("role") == "user"
                and isinstance(turn.get("text"), str) and turn["text"].strip()
            ]
            question = user_turns[-1]["text"].strip() if user_turns else ""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question 或 input.query 必须是非空字符串")
    if query is not None and query != question:
        raise ValueError("question 与 input.query 不一致")
    case_input.setdefault("query", question.strip())
    if not isinstance(expected, dict) or len(json.dumps(expected, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise ValueError("expected 必须是 JSON 对象且不超过 64 KB")
    if not isinstance(metadata, dict) or len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
        raise ValueError("metadata 必须是 JSON 对象且不超过 16 KB")
    normalized_metadata = dict(metadata)
    for field in ("tier", "query_type", "tags", "splits"):
        if field in value:
            normalized_metadata[field] = value[field]
    return {
        "id": case_id.strip(),
        "question": question.strip(),
        "input": case_input,
        "expected": expected,
        "metadata": normalized_metadata,
    }
