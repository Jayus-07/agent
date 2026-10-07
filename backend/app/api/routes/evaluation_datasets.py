"""评测集治理 API：目录、候选审核、不可变版本和 Suite 范围。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.evaluation.dataset.loader import DATASET_DIR
from backend.evaluation.models import MODULE_KINDS, PROBE_MODULES
from backend.shared.logger import logger

router = APIRouter(prefix="/evaluation", tags=["评测集治理"])

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CANDIDATE_ROOT = _PROJECT_ROOT / "data" / "eval_dataset_candidates"
_DEFAULT_VERSION_ROOT = _PROJECT_ROOT / "data" / "eval_dataset_versions"
CANDIDATE_ROOT = Path(os.getenv("EVAL_DATASET_CANDIDATE_DIR", str(_DEFAULT_CANDIDATE_ROOT)))
VERSION_ROOT = Path(os.getenv("EVAL_DATASET_VERSION_DIR", str(_DEFAULT_VERSION_ROOT)))
_candidate_store: dict[str, "DatasetCandidate"] = {}


class DatasetCandidate(BaseModel):
    candidate_id: str
    module: str
    question: str
    expected: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "trace"
    redacted: bool = False
    owner: str = "evaluation-platform"
    status: str = "pending_review"
    dataset_version: str = ""
    created_at: str = Field(default_factory=lambda: _now())
    reviewer: str = ""
    review_reason: str = ""
    approved_version: str = ""
    content_hash: str = ""


class ReviewCandidateRequest(BaseModel):
    reviewer: str = Field(min_length=1)
    reason: str = ""


class TraceCandidateRequest(BaseModel):
    trace_id: str = Field(min_length=1)
    module: str = "rag"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_dataset_candidate_store() -> None:
    """测试和本地开发使用的候选缓存清理入口。"""
    _candidate_store.clear()


def register_dataset_candidate(
    *,
    candidate_id: str,
    module: str,
    question: str,
    expected: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    source_type: str = "trace",
    redacted: bool = False,
    owner: str = "evaluation-platform",
    dataset_version: str = "",
) -> DatasetCandidate:
    """注册待审核候选，供 Trace/反馈/合成数据适配器调用。"""
    candidate = DatasetCandidate(
        candidate_id=candidate_id,
        module=module,
        question=question,
        expected=expected or {},
        metadata=metadata or {},
        source_type=source_type,
        redacted=redacted,
        owner=owner,
        dataset_version=dataset_version,
    )
    _candidate_store[candidate_id] = candidate
    _persist_candidate(candidate)
    return candidate


def _persist_candidate(candidate: DatasetCandidate) -> None:
    try:
        CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
        path = CANDIDATE_ROOT / f"{candidate.candidate_id}.json"
        path.write_text(candidate.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("候选评测集持久化失败（不影响当前审核请求）: %s", exc)


def _load_candidates() -> dict[str, DatasetCandidate]:
    if _candidate_store:
        return _candidate_store
    if not CANDIDATE_ROOT.exists():
        return _candidate_store
    for path in CANDIDATE_ROOT.glob("*.json"):
        try:
            candidate = DatasetCandidate.model_validate_json(path.read_text(encoding="utf-8"))
            _candidate_store[candidate.candidate_id] = candidate
        except (OSError, ValueError) as exc:
            logger.warning("跳过非法评测集候选 %s: %s", path, exc)
    return _candidate_store


def _candidate_payload(candidate: DatasetCandidate) -> dict[str, Any]:
    payload = candidate.model_dump(mode="json")
    # 普通编辑者只看到经过审核的标注摘要，不返回疑似敏感字段的原值。
    payload["metadata"] = _redact_mapping(candidate.metadata)
    payload["expected"] = _redact_mapping(candidate.expected)
    return payload


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    sensitive = ("token", "secret", "password", "api_key", "手机号", "phone", "email")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if any(word in str(key).lower() for word in sensitive):
            result[key] = "[REDACTED]"
        elif isinstance(item, dict):
            result[key] = _redact_mapping(item)
        elif isinstance(item, list):
            result[key] = [
                _redact_mapping(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            result[key] = item
    return result


def _redact_text(value: str) -> str:
    """候选入队前先做最小化 PII 脱敏，原始 Trace 不返回给普通编辑者。"""
    value = re.sub(r"\b1[3-9]\d{9}\b", "[PHONE_REDACTED]", value)
    value = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[EMAIL_REDACTED]", value)
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._-]+", "Bearer [TOKEN_REDACTED]", value)
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _find_trace(trace_id: str) -> Any | None:
    try:
        from backend.observability.tracer import trace_collector

        for trace in trace_collector.list(limit=200):
            current_id = trace.get("id") if isinstance(trace, dict) else getattr(trace, "id", "")
            if current_id == trace_id:
                return trace
    except Exception as exc:  # noqa: BLE001 — 候选入口软失败
        logger.warning("读取 Trace 候选失败: %s", exc)
    return None


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default or {}
    return value if isinstance(value, dict) else (default or {})


def _read_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    cases.append(value)
    except (OSError, json.JSONDecodeError):
        return []
    return cases


def _content_hash(manifest: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    payload = {"manifest": manifest, "cases": cases}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    tiers = Counter(str(case.get("metadata", {}).get("tier", "unknown")) for case in cases)
    sources = Counter(str(case.get("metadata", {}).get("source", "unknown")) for case in cases)
    query_types = Counter(str(case.get("metadata", {}).get("query_type", "unknown")) for case in cases)
    return {
        "tiers": dict(tiers),
        "sources": dict(sources),
        "query_types": dict(query_types),
        "verified_count": sum(
            bool(case.get("metadata", {}).get("ground_truth_verified")) for case in cases
        ),
    }


def _latest_run(module: str) -> dict[str, Any] | None:
    try:
        from backend.evaluation.storage import list_runs, load_report

        for run_id in list_runs(limit=30):
            report, meta = load_report(run_id)
            if report.module == module:
                return {
                    "run_id": run_id,
                    "pass_rate": next(
                        (summary.pass_rate for summary in report.summaries if summary.module == module),
                        0.0,
                    ),
                    "timestamp": report.timestamp,
                    "provenance": meta.get("eval_provenance", {}),
                }
    except Exception as exc:  # noqa: BLE001 — 目录页不能被历史报告损坏阻断
        logger.debug("读取最新评测运行失败: %s", exc)
    return None


def _catalog_item(module: str, root: Path | None = None) -> dict[str, Any]:
    module_root = (root or DATASET_DIR) / module
    manifest = _read_json(module_root / "manifest.json")
    cases = _read_cases(module_root / "cases.jsonl")
    version = str(manifest.get("version") or "unversioned")
    digest = _content_hash(manifest, cases)
    return {
        "dataset_id": module,
        "module": module,
        # 探针模块打 probe 标签（口径唯一源 models.PROBE_MODULES），前端分组展示
        "kind": "probe" if module in PROBE_MODULES else "core",
        "dataset_version": version,
        "owner": str(manifest.get("owner", "evaluation-platform")),
        "review_status": str(manifest.get("review_status", "approved")),
        "case_count": int(manifest.get("case_count") or len(cases)),
        "content_hash": digest,
        "coverage": _coverage(cases),
        "kb_id": manifest.get("kb_id", ""),
        "fixture_set": manifest.get("fixture_set", ""),
        "latest_run": _latest_run(module),
    }


def _suite_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not DATASET_DIR.exists():
        return items
    for suite_path in sorted(DATASET_DIR.glob("*/suites/*.json")):
        suite = _read_json(suite_path)
        items.append({
            "name": str(suite.get("name", suite_path.stem)),
            "module": suite_path.parent.parent.name,
            "version": str(suite.get("version", "")),
            "dataset_version": str(suite.get("dataset_version", "")),
            "kb_id": str(suite.get("kb_id", "")),
            "fixture_set": str(suite.get("fixture_set", "")),
            "case_count": len(suite.get("case_ids", [])),
            "case_ids": suite.get("case_ids", []),
            "trigger_classes": suite.get("trigger_classes", ["manual", "prompt_publish"]),
            "thresholds": suite.get("thresholds", {}),
            "prompt_mapping": suite.get("prompt_mapping", {}),
            "tool_mapping": suite.get("tool_mapping", {}),
        })
    return items


def _stage_candidate(candidate: DatasetCandidate, reviewer: str) -> DatasetCandidate:
    module_root = DATASET_DIR / candidate.module
    manifest = _read_json(module_root / "manifest.json")
    cases = _read_cases(module_root / "cases.jsonl")
    parent_version = str(manifest.get("version") or candidate.dataset_version or "unversioned")
    new_case = {
        "id": candidate.candidate_id,
        "question": candidate.question,
        "module": candidate.module,
        "expected": candidate.expected,
        "metadata": {
            **candidate.metadata,
            "source": candidate.source_type,
            "ground_truth_verified": True,
            "dataset_parent_version": parent_version,
        },
    }
    staged_cases = [*cases, new_case]
    digest = _content_hash({"parent_version": parent_version}, staged_cases)
    version = f"{parent_version}+candidate.{digest[:8]}"
    version_root = VERSION_ROOT / candidate.module / version
    version_root.mkdir(parents=True, exist_ok=True)
    staged_manifest = {
        **manifest,
        "version": version,
        "parent_version": parent_version,
        "case_count": len(staged_cases),
        "content_hash": digest,
        "review_status": "approved",
        "immutable": True,
        "owner": candidate.owner,
        "reviewer": reviewer,
        "approved_at": _now(),
        "source_type": candidate.source_type,
    }
    (version_root / "cases.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in staged_cases) + "\n",
        encoding="utf-8",
    )
    (version_root / "manifest.json").write_text(
        json.dumps(staged_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    candidate.status = "approved"
    candidate.reviewer = reviewer
    candidate.approved_version = version
    candidate.content_hash = digest
    candidate.review_reason = "approved"
    _persist_candidate(candidate)
    return candidate


@router.get("/datasets")
async def list_datasets(module: str | None = Query(default=None)):
    # 目录扫描只认 MODULE_KINDS 声明过的模块（sql_v2/travel_v2 等专项金标集
    # 由 pytest/验收脚本直读，不进评测框架口径，不刷目录页）；
    # 显式点名 module 时不过滤，治理侧仍可单独查看。
    known_modules = frozenset(MODULE_KINDS)
    modules = [module] if module else sorted(
        path.name for path in DATASET_DIR.iterdir()
        if path.is_dir() and (path / "cases.jsonl").exists() and path.name in known_modules
    ) if DATASET_DIR.exists() else []
    return {"items": [_catalog_item(item) for item in modules]}


@router.get("/datasets/{dataset_id}/versions/{version}")
async def get_dataset_version(dataset_id: str, version: str):
    canonical_root = DATASET_DIR / dataset_id
    manifest = _read_json(canonical_root / "manifest.json")
    cases_path = canonical_root / "cases.jsonl"
    if str(manifest.get("version", "")) != version:
        staged_root = VERSION_ROOT / dataset_id / version
        manifest = _read_json(staged_root / "manifest.json")
        cases_path = staged_root / "cases.jsonl"
    if not manifest:
        raise HTTPException(status_code=404, detail="评测集版本不存在")
    cases = _read_cases(cases_path)
    suites = [item for item in _suite_items() if item["module"] == dataset_id and (
        item["dataset_version"] == version or any(
            case_id in {case.get("id") for case in cases} for case_id in item["case_ids"]
        )
    )]
    return {
        "dataset_id": dataset_id,
        "version": version,
        "immutable": bool(manifest.get("immutable", True)),
        "owner": manifest.get("owner", "evaluation-platform"),
        "review_status": manifest.get("review_status", "approved"),
        "case_count": len(cases),
        "content_hash": manifest.get("content_hash") or _content_hash(manifest, cases),
        "metadata": manifest,
        "coverage": _coverage(cases),
        "case_diff": {"added": [case.get("id") for case in cases if str(case.get("id", "")).startswith("cand-")], "removed": [], "changed": []},
        "suite_membership": suites,
        "audit": [{"action": "version_created", "actor": manifest.get("reviewer", "system"), "at": manifest.get("approved_at", "")}],
    }


@router.get("/dataset-candidates")
async def list_dataset_candidates(status: str | None = Query(default=None)):
    candidates = list(_load_candidates().values())
    if status:
        candidates = [item for item in candidates if item.status == status]
    return {"items": [_candidate_payload(item) for item in candidates]}


@router.post("/dataset-candidates/from-trace", status_code=202)
async def stage_trace_candidate(
    body: TraceCandidateRequest,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """把线上 Trace 转为已脱敏候选，必须再经过人工审核才能生成版本。"""
    trace = _find_trace(body.trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace 不存在")
    get = lambda key, default=None: trace.get(key, default) if isinstance(trace, dict) else getattr(trace, key, default)
    raw_question = str(get("question", "") or "")
    if not raw_question.strip():
        raise HTTPException(status_code=422, detail="Trace 缺少问题文本")
    raw_answer = str(get("answer_preview", "") or "")
    candidate = register_dataset_candidate(
        candidate_id=f"trace-{uuid.uuid4().hex[:12]}",
        module=body.module,
        question=_redact_text(raw_question),
        expected=_redact_value({"expected_answer": raw_answer}) if raw_answer else {},
        metadata={"source": "production_log", "trace_id": body.trace_id, "redaction_policy": "pii-v1"},
        source_type="trace",
        redacted=True,
        owner="trace-curator",
    )
    return _candidate_payload(candidate)


@router.post("/dataset-candidates/{candidate_id}/approve", status_code=201)
async def approve_dataset_candidate(
    candidate_id: str,
    body: ReviewCandidateRequest,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    candidate = _load_candidates().get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="评测集候选不存在")
    if not candidate.redacted:
        return JSONResponse(
            status_code=409,
            content={
                "code": "DATASET_REDACTION_REQUIRED",
                "message": "候选仍包含未完成脱敏的 Trace/反馈内容",
            },
        )
    if candidate.status == "approved":
        return _candidate_payload(candidate)
    if not candidate.question.strip():
        raise HTTPException(status_code=422, detail="候选问题不能为空")
    return _candidate_payload(_stage_candidate(candidate, body.reviewer))


@router.post("/dataset-candidates/{candidate_id}/reject")
async def reject_dataset_candidate(
    candidate_id: str,
    body: ReviewCandidateRequest,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    candidate = _load_candidates().get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="评测集候选不存在")
    candidate.status = "rejected"
    candidate.reviewer = body.reviewer
    candidate.review_reason = body.reason or "审核未通过"
    _persist_candidate(candidate)
    return _candidate_payload(candidate)


@router.get("/suites")
async def list_evaluation_suites():
    return {"items": _suite_items()}


__all__ = [
    "CANDIDATE_ROOT",
    "DATASET_DIR",
    "VERSION_ROOT",
    "DatasetCandidate",
    "clear_dataset_candidate_store",
    "register_dataset_candidate",
    "router",
]
