"""上传评测资产：严格格式、不可变内容和知识库绑定。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_BYTES = 5 * 1024 * 1024
MAX_CASES = 100


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Manifest(StrictModel):
    schema_version: Literal["rag-eval/1.0"] = "rag-eval/1.0"
    dataset_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    module: Literal["rag"] = "rag"
    description: str = Field(default="", max_length=2000)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    case_schema: dict = Field(default_factory=dict)
    csv_mapping: dict[str, str] = Field(default_factory=dict, max_length=20)


class Evidence(StrictModel):
    doc_id: str = Field(default="", max_length=200)
    source_file: str = Field(default="", max_length=255)
    quote: str = Field(min_length=1, max_length=8000)
    version_id: str = Field(default="", max_length=200)


class Expected(StrictModel):
    behavior: Literal["answer", "refuse"]
    reason: Literal["no_evidence", "permission_denied"] | None = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    reference_answer: str = Field(default="", max_length=8000)
    required_facts: list[str] = Field(default_factory=list, max_length=30)
    assertions: list[dict] = Field(default_factory=list, max_length=20)


class ImportCase(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    question: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    tier: Literal["smoke", "core", "hard", "regression"] = "core"
    query_type: Literal["single_doc", "multi_doc", "table", "procedural", "no_evidence", "permission_denied"] = (
        "single_doc"
    )
    expected: Expected
    splits: list[str] = Field(default_factory=list, max_length=20)
    archived: bool = False


def root() -> Path:
    """复用 app_data 持久卷；不把用户数据写进内置数据集目录。"""
    default = Path(__file__).resolve().parents[3] / "data"
    return Path(os.getenv("RAG_DATA_DIR", str(default))) / "eval_dataset_imports"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read(kind: str, identity: str) -> dict:
    if kind not in {
        "imports",
        "reviews",
        "bindings",
        "versionkeys",
        "executions",
        "labels",
        "lineage",
        "snapshots",
        "gatepolicies",
    } or not re.fullmatch(r"[0-9a-f]{24}", identity):
        raise ValueError("资产标识无效")
    return json.loads((root() / kind / f"{identity}.json").read_text(encoding="utf-8"))


def write_once(kind: str, identity: str, value: dict) -> dict:
    """完整写入并 fsync 后原子发布；同一身份不可覆盖。"""
    directory = root() / kind
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{identity}.json"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".stage-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return read(kind, identity)
        return value
    finally:
        Path(temporary).unlink(missing_ok=True)


def list_records(kind: str) -> list[dict]:
    directory = root() / kind
    if not directory.exists():
        return []
    # 损坏的权威记录显式失败，不静默跳过。
    return [read(kind, path.stem) for path in sorted(directory.glob("*.json"))]


def validate_upload(filename: str, data: bytes, manifest_data: dict) -> dict:
    """校验完整文件；所有行都检查，响应中的预览与错误条数有上限。"""
    errors: list[dict] = []
    cases: list[dict] = []
    if len(data) > MAX_BYTES:
        return {"valid": False, "errors": [{"line": 0, "field": "file", "message": "文件超过 5 MB"}]}
    try:
        text = data.decode("utf-8-sig")
        if not isinstance(manifest_data, dict):
            raise ValueError("manifest 必须为 JSON 对象")
        if filename.lower().endswith(".csv"):
            reader = csv.DictReader(io.StringIO(text), strict=True)
            allowed = set(ImportCase.model_fields)
            if reader.fieldnames and len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("CSV 表头重复，无法确认列含义")
            mapping = manifest_data.get("csv_mapping", {})
            if mapping and (
                not isinstance(mapping, dict)
                or set(mapping) - allowed
                or any(not isinstance(v, str) for v in mapping.values())
                or len(set(mapping.values())) != len(mapping)
            ):
                raise ValueError("CSV 映射需要标准字段到唯一来源表头的对象")
            if (
                not reader.fieldnames
                or (not mapping and set(reader.fieldnames) - allowed)
                or (mapping and set(mapping.values()) - set(reader.fieldnames))
            ):
                raise ValueError("CSV 表头须使用标准字段名；expected/tags/splits 使用 JSON")
            rows = []
            for row in reader:
                if None in row:
                    raise ValueError(f"CSV 第 {reader.line_num} 行列数超过表头")
                mapped = {key: row[value] for key, value in mapping.items()} if mapping else row
                parsed = {key: value for key, value in mapped.items() if value != ""}
                try:
                    for key in ("expected", "tags", "splits", "archived"):
                        if key in parsed:
                            parsed[key] = json.loads(parsed[key])
                except ValueError:
                    errors.append({"line": reader.line_num, "field": key, "message": "CSV 单元格中的 JSON 语法错误"})
                    continue
                rows.append((reader.line_num, parsed))
        elif filename.lower().endswith(".jsonl"):
            rows = []
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rows.append((number, json.loads(line)))
                except ValueError:
                    errors.append({"line": number, "field": "json", "message": "JSON 语法错误"})
        elif filename.lower().endswith(".json"):
            payload = json.loads(text)
            if not isinstance(payload, dict) or set(payload) != {"manifest", "cases"}:
                raise ValueError("JSON 顶层必须包含 manifest 和 cases")
            manifest_data = payload["manifest"]
            if not isinstance(payload["cases"], list):
                raise ValueError("cases 必须为数组")
            rows = list(enumerate(payload["cases"], 1))
        else:
            raise ValueError("仅支持 .jsonl、.json 和 .csv 文件")
        manifest = Manifest.model_validate(manifest_data).model_dump()
        schema = manifest["case_schema"]
        if schema:
            if len(json.dumps(schema, allow_nan=False)) > 16000:
                raise ValueError("自定义 Schema 超过 16 KB")

            # 禁止远端引用，校验器不具有网络与本地文件读取能力。
            def check_schema(value, depth=0):
                if depth > 12:
                    raise ValueError("Schema 嵌套超过 12 层")
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in {"$ref", "$dynamicRef", "pattern", "patternProperties"}:
                            raise ValueError("Schema 不支持引用及正则表达式")
                        check_schema(child, depth + 1)
                elif isinstance(value, list):
                    for child in value:
                        check_schema(child, depth + 1)

            check_schema(schema)
            Draft202012Validator.check_schema(schema)
    except (UnicodeError, ValueError, TypeError, SchemaError, csv.Error) as exc:
        return {"valid": False, "errors": [{"line": 0, "field": "file/manifest", "message": str(exc)[:500]}]}
    if not 1 <= len(rows) <= MAX_CASES:
        errors.append({"line": 0, "field": "cases", "message": "每个版本需要 1–100 条用例"})
    seen: set[str] = set()
    for number, row in rows:
        try:
            case = ImportCase.model_validate(row)
            if schema:
                issues = list(Draft202012Validator(schema).iter_errors(case.model_dump()))
                if issues:
                    raise ValueError("自定义 Schema: " + issues[0].message[:300])
            if case.id in seen:
                raise ValueError("用例 ID 重复")
            seen.add(case.id)
            if not case.question.strip():
                raise ValueError("问题不能仅包含空白")
            expected = case.expected
            for assertion in expected.assertions:
                if (
                    set(assertion) != {"type", "value"}
                    or assertion["type"] not in {"contains", "not_contains", "equals"}
                    or not isinstance(assertion["value"], str)
                    or not assertion["value"].strip()
                ):
                    raise ValueError("断言需要 type=contains/not_contains/equals 与非空字符串 value")
            if expected.behavior == "answer":
                if not expected.evidence or expected.reason is not None or case.query_type == "no_evidence":
                    raise ValueError("回答题需要证据，且不能声明拒答原因/无答案题型")
                for evidence in expected.evidence:
                    if not (evidence.doc_id.strip() or evidence.source_file.strip()) or not evidence.quote.strip():
                        raise ValueError("每条证据需要 doc_id 或 source_file，以及非空 quote")
            elif (
                expected.evidence
                or expected.reason not in {"no_evidence", "permission_denied"}
                or case.query_type != expected.reason
                or expected.required_facts
                or expected.reference_answer
                or expected.assertions
            ):
                raise ValueError("拒答题需要 no_evidence 题型/原因，证据、参考答案和必须事实须为空")
            cases.append(case.model_dump())
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False):
                errors.append({"line": number, "field": ".".join(map(str, error["loc"])), "message": error["msg"]})
        except ValueError as exc:
            errors.append({"line": number, "field": "case", "message": str(exc)})
    return {"valid": not errors, "manifest": manifest, "cases": cases, "case_count": len(rows), "errors": errors[:100]}


def create_import(validation: dict, actor: str) -> dict:
    content = {"manifest": validation["manifest"], "cases": validation["cases"]}
    content_hash = digest(content)
    identity = content_hash[:24]
    version_key = digest({"key": content["manifest"]["dataset_key"], "version": content["manifest"]["version"]})[:24]
    claimed = write_once("versionkeys", version_key, {"content_hash": content_hash})
    if claimed["content_hash"] != content_hash:
        raise ValueError("同一数据集版本已有不同内容，请增加版本号后再提交")
    record = write_once(
        "imports",
        identity,
        {
            **content,
            "import_id": identity,
            "content_hash": content_hash,
            "owner": actor,
            "created_at": now(),
        },
    )
    return summary(record)


def approved_import(identity: str) -> dict:
    record = read("imports", identity)
    content = {"manifest": record["manifest"], "cases": record["cases"]}
    if digest(content) != record["content_hash"] or record["content_hash"][:24] != identity:
        raise ValueError("数据集内容哈希不一致")
    if read("reviews", identity)["decision"] != "approved":
        raise ValueError("评测集尚未审核通过")
    return record


def summary(record: dict) -> dict:
    try:
        review = read("reviews", record["import_id"])
    except FileNotFoundError:
        review = {"decision": "pending_review"}
    return {key: value for key, value in record.items() if key != "cases"} | {
        "case_count": len(record["cases"]),
        "active_case_count": sum(not case.get("archived") for case in record["cases"]),
        "review": review,
        "preview": record["cases"][:3],
    }


def load_binding(binding_id: str) -> tuple[dict, dict]:
    binding = read("bindings", binding_id)
    return binding, approved_import(binding["import_id"])


def normalized_cases(binding_id: str):
    from backend.evaluation.models import TestCase

    binding, record = load_binding(binding_id)
    subject = None
    if binding.get("subject_user_id"):
        from backend.security.task_authorization import resolve_task_authorization

        authorization = resolve_task_authorization(str(binding["subject_user_id"]), "default")
        principal = authorization.principal
        subject = {
            "user_id": principal.user_id,
            "department": principal.department,
            "roles": list(principal.roles),
            "permissions": principal.permissions,
            "kb_allowed": authorization.can_access_kb(binding["kb_id"]),
        }
    result = []
    for case in record["cases"]:
        if case.get("archived"):
            continue
        if binding.get("split") and binding["split"] not in case.get("splits", []):
            continue
        expected = case["expected"]
        result.append(
            TestCase(
                id=case["id"],
                question=case["question"],
                module="rag",
                expected={
                    "relevant_docs": [
                        {"doc_id": e["doc_id"], "source_file": e["source_file"]} for e in expected["evidence"]
                    ],
                    "ground_truth_context": [
                        {"text": e["quote"], "source_doc": e["doc_id"] or e["source_file"]}
                        for e in expected["evidence"]
                    ],
                    "expected_answer": expected["reference_answer"],
                    "required_facts": expected["required_facts"],
                    "should_reject": expected["behavior"] == "refuse",
                    "reject_reason": expected["reason"],
                    "match_type": "snippet",
                    "relevant_snippets": [e["quote"] for e in expected["evidence"]],
                },
                metadata={
                    "kb_id": binding["kb_id"],
                    "fixture_set": "",
                    "tier": case["tier"],
                    "query_type": case["query_type"],
                    "source": "curated",
                    "dataset_version": record["manifest"]["version"],
                    "suite": f"import:{binding_id}",
                    "suite_content_hash": record["content_hash"],
                    "managed_binding_id": binding_id,
                    "generation_eval": binding.get("mode") == "answer",
                    "assertions": expected.get("assertions", []),
                    **({"evaluation_subject": subject, "department": subject["department"]} if subject else {}),
                },
            )
        )
    return result


def corpus_snapshot(kb_id: str) -> list[dict]:
    from backend.rag.indexing.doc_registry import DocumentRegistry

    fields = ("doc_id", "file_name", "file_path", "file_hash", "version_id", "active_generation", "index_generation")
    rows = DocumentRegistry().list_by_kb(kb_id)
    snapshot = [{key: str(row.get(key) or "") for key in fields} for row in rows if row.get("status") == "active"]
    if not snapshot:
        raise ValueError("知识库没有已发布文档，不能开始评测")
    return sorted(snapshot, key=lambda item: item["doc_id"])


def check_evidence(record: dict, snapshot: list[dict]) -> None:
    """先确认稳定文档与版本可映射，原文引用命中由检索结果评测。"""
    for case in record["cases"]:
        if case.get("archived"):
            continue
        for evidence in case["expected"]["evidence"]:
            matches = [
                row
                for row in snapshot
                if (
                    (not evidence["doc_id"] or evidence["doc_id"] == row["doc_id"])
                    and (
                        not evidence["source_file"]
                        or evidence["source_file"] == (row["file_name"] or Path(row["file_path"]).name)
                    )
                    and (not evidence["version_id"] or evidence["version_id"] == row["version_id"])
                )
            ]
            if len(matches) != 1:
                raise ValueError(f"{case['id']} 的证据文档或版本无法唯一映射，请使用当前知识库的 doc_id")
