"""评测平台 V2 API：可信租户、候选审核与不可变版本。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.api.deps import OperatorIdentity, require_user_actor
from backend.app.api.identity import Identity, require_principal, require_product_identity
from backend.evaluation_v2 import repository
from backend.evaluation_v2.capabilities import (
    UnsupportedRunnerCapabilityError,
    require_supported_runner_module,
)
from backend.evaluation_v2.domain_contracts import (
    validate_dataset_domain,
    validate_non_rag_case,
)
from backend.evaluation_v2.imports import validate_non_rag_upload
from backend.evaluation_v2.privacy import sanitize_candidate_payload

router = APIRouter(prefix="/evaluation/v2", tags=["评测平台 V2"])
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "super_admin": 3}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetCreate(StrictModel):
    dataset_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    name: str = Field(min_length=1, max_length=160)
    domain: str = Field(default="rag", min_length=1, max_length=64)
    description: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_module_domain(self) -> "DatasetCreate":
        validate_dataset_domain(self.domain)
        return self


class CandidatePayload(StrictModel):
    input: dict[str, Any]
    observed_answer: str = Field(default="", max_length=12000)
    proposed_facts: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    proposed_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_ref: dict[str, Any] = Field(default_factory=dict)
    source_content_hash: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def enforce_size_limit(self) -> "CandidatePayload":
        import json

        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > 128 * 1024:
            raise ValueError("单条评测候选不得超过 128 KB")
        return self


class CandidateCreate(StrictModel):
    source_kind: Literal["manual", "import", "document_synthesized", "feedback"]
    payload: CandidatePayload
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class CaseRevisionCandidateCreate(StrictModel):
    query: str = Field(min_length=1, max_length=12000)
    observed_answer: str = Field(default="", max_length=12000)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class EvidenceAnchorClaim(StrictModel):
    kb_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    doc_id: str = Field(min_length=1, max_length=200)
    doc_version: str = Field(min_length=1, max_length=200)
    anchor_id: str | None = Field(default=None, min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=8000)


class CandidateReview(StrictModel):
    expected_revision: int = Field(ge=1)
    verdict: Literal["approved", "rejected", "needs_changes"]
    rationale: str = Field(min_length=1, max_length=2000)
    expected: dict[str, Any] | None = None
    evidence: list[EvidenceAnchorClaim] = Field(default_factory=list, max_length=30)


class DatasetItemRef(StrictModel):
    case_id: uuid.UUID
    revision: int = Field(ge=1)


class DatasetPublish(StrictModel):
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    items: list[DatasetItemRef] = Field(min_length=1, max_length=20000)


class EvaluatorVersionRef(StrictModel):
    evaluator_version_id: uuid.UUID
    binding: str = Field(min_length=1, max_length=64)


class EvaluatorCreate(StrictModel):
    evaluator_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    evaluator_kind: Literal["deterministic", "evidence", "llm_judge", "human"]
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_config_size(self) -> "EvaluatorCreate":
        encoded = json.dumps(self.config, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("评分器配置不得超过 64 KB")
        if self.evaluator_kind == "human":
            repository.normalize_human_rubric(self.config)
        return self


class SuiteCreate(StrictModel):
    suite_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    name: str = Field(min_length=1, max_length=160)
    module: str = Field(min_length=1, max_length=64)
    dataset_version_id: uuid.UUID
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    case_selection: dict[str, Any] = Field(default_factory=dict)
    evaluator_versions: list[EvaluatorVersionRef] = Field(min_length=1, max_length=50)
    metric_policy: dict[str, Any] = Field(default_factory=dict)
    security_policy: dict[str, Any] = Field(default_factory=dict)
    minimum_valid_samples: int = Field(ge=1, le=20000)

    @model_validator(mode="after")
    def enforce_config_size(self) -> "SuiteCreate":
        require_supported_runner_module(self.module)
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > 128 * 1024:
            raise ValueError("评测方案配置不得超过 128 KB")
        return self


class SuiteVersionCreate(StrictModel):
    dataset_version_id: uuid.UUID
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    case_selection: dict[str, Any] = Field(default_factory=dict)
    evaluator_versions: list[EvaluatorVersionRef] = Field(min_length=1, max_length=50)
    metric_policy: dict[str, Any] = Field(default_factory=dict)
    security_policy: dict[str, Any] = Field(default_factory=dict)
    minimum_valid_samples: int = Field(ge=1, le=20000)

    @model_validator(mode="after")
    def enforce_config_size(self) -> "SuiteVersionCreate":
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > 128 * 1024:
            raise ValueError("评测方案配置不得超过 128 KB")
        return self


class GateApprovalCreate(StrictModel):
    gate_decision_id: uuid.UUID
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    decision: Literal["approved", "rejected"]
    release_reference: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}$",
    )
    rationale: str = Field(min_length=8, max_length=2000)

    @model_validator(mode="after")
    def validate_rationale(self) -> "GateApprovalCreate":
        if len(self.rationale.strip()) < 8:
            raise ValueError("审批理由至少需要 8 个非空字符")
        return self


class HumanReviewCreate(StrictModel):
    evaluator_version_id: uuid.UUID
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    label: Literal["correct", "partially_correct", "incorrect", "unsafe", "unverifiable"]
    scores: dict[str, float] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=8, max_length=2000)

    @model_validator(mode="after")
    def validate_review(self) -> "HumanReviewCreate":
        if len(self.rationale.strip()) < 8:
            raise ValueError("人工复核理由至少需要 8 个非空字符")
        for key, score in self.scores.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("人工评分维度 key 格式无效")
            if isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("人工评分必须是 0 到 1 的有限数值")
        return self


class TraceIntake(StrictModel):
    trace_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")


class EvidenceSearchCreate(StrictModel):
    snapshot_id: str = Field(min_length=8, max_length=160)
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)


class EvalRunCreate(StrictModel):
    suite_version_id: uuid.UUID
    source: str = Field(default="manual", pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    evaluation_mode: str = Field(
        default="self+ragas",
        pattern=r"^(offline|semantic|ragas|self\+ragas)$",
    )
    kb_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    subject_user_id: int | None = Field(default=None, gt=0)
    prompt_versions: dict[str, int | str | None] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def validate_prompt_versions(self) -> "EvalRunCreate":
        for key, version in self.prompt_versions.items():
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", key):
                raise ValueError("Prompt 标识格式无效")
            if isinstance(version, str) and (not version.strip() or len(version) > 128):
                raise ValueError("Prompt 版本必须是非空短标识")
            if isinstance(version, int) and version < 1:
                raise ValueError("Prompt 版本号必须大于零")
        return self


class ExperimentCreate(StrictModel):
    suite_version_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)


class ExperimentRunAttach(StrictModel):
    run_id: uuid.UUID
    slot: Literal["baseline", "candidate"]


def _role(actor: OperatorIdentity, minimum: str) -> None:
    if _ROLE_RANK.get(actor.role, -1) < _ROLE_RANK[minimum]:
        raise HTTPException(403, "当前角色无权执行此评测操作")


def _build_run_evaluator_snapshot(
    evaluator_versions: list[dict[str, Any]],
    evaluator_definitions: list[dict[str, Any]],
    *,
    module: str = "rag",
) -> list[dict[str, Any]]:
    """冻结机器 Runner 绑定与可选人工 Rubric；人工评分不进入自动 Gate。"""
    allowed_bindings = (
        {"answer_quality", "human_review"}
        if module != "rag"
        else {"answer_quality", "evidence_quality", "human_review"}
    )
    machine_bindings = {"answer_quality"} if module != "rag" else {"answer_quality", "evidence_quality"}
    if (
        not evaluator_versions
        or any(ref.get("binding") not in allowed_bindings for ref in evaluator_versions)
        or not any(ref.get("binding") in machine_bindings for ref in evaluator_versions)
    ):
        raise ValueError("运行至少需要一个机器评分器；人工复核只能作为补充评分绑定")
    expected_refs = {
        (str(ref.get("evaluator_version_id", "")), ref.get("binding"))
        for ref in evaluator_versions
    }
    actual_refs = {
        (str(item.get("evaluator_version_id", "")), item.get("binding"))
        for item in evaluator_definitions
    }
    if len(actual_refs) != len(evaluator_versions) or actual_refs != expected_refs:
        raise ValueError("运行评分器定义与发布 Suite 快照不一致")

    snapshots: list[dict[str, Any]] = []
    for evaluator in evaluator_definitions:
        binding = evaluator["binding"]
        common = {
            "evaluator_version_id": str(evaluator["evaluator_version_id"]),
            "evaluator_key": evaluator["evaluator_key"],
            "version": evaluator["version"],
            "evaluator_kind": evaluator["evaluator_kind"],
            "rubric_hash": str(evaluator["rubric_hash"]),
            "binding": binding,
        }
        config = evaluator.get("config_json") or {}
        if isinstance(config, str):
            config = json.loads(config)
        if binding == "human_review":
            if evaluator["evaluator_kind"] != "human":
                raise ValueError("human_review 绑定必须使用人工评分器")
            snapshots.append({
                **common,
                "adapter": "manual-review-v1",
                "config": repository.normalize_human_rubric(config),
            })
            continue

        if evaluator["evaluator_kind"] not in {"deterministic", "evidence"}:
            raise ValueError("当前 Runner adapter 尚不支持此机器评分器或 LLM Judge")
        if binding == "answer_quality" and evaluator["evaluator_kind"] != "deterministic":
            raise ValueError("answer_quality 绑定必须使用确定性评分器")
        if binding == "evidence_quality" and evaluator["evaluator_kind"] != "evidence":
            raise ValueError("evidence_quality 绑定必须使用证据评分器")
        if not isinstance(config, dict) or set(config) - {"adapter"}:
            raise ValueError("当前 Runner adapter 不支持自定义评分器配置")
        adapter = (
            f"legacy-{module}-result-status-v1"
            if module != "rag"
            else (
                "legacy-rag-answer-status-v1"
                if binding == "answer_quality"
                else "legacy-rag-evidence-metrics-v1"
            )
        )
        if config.get("adapter", adapter) != adapter:
            raise ValueError("评分器版本声明的 Runner adapter 不兼容")
        snapshots.append({**common, "adapter": adapter})
    return snapshots


def _safe_error(exc: Exception) -> None:
    from psycopg2.errors import UniqueViolation

    from backend.evaluation.corpus_snapshots import EmptyCorpusError

    if isinstance(exc, UnsupportedRunnerCapabilityError):
        error = HTTPException(422, "当前 V2 Runner 不支持该方案配置")
        error.user_message = str(exc)
        raise error from exc
    if isinstance(exc, EmptyCorpusError):
        error = HTTPException(422, "知识库没有已发布文档")
        error.user_message = "该知识库没有已发布文档，请先完成文档发布和索引后再运行评测。"
        raise error from exc
    if isinstance(exc, repository.RepositoryExportLimitError):
        raise HTTPException(413, str(exc)) from exc
    if isinstance(exc, repository.RepositoryNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, (repository.RepositoryConflictError, UniqueViolation)):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(422, str(exc)) from exc
    raise HTTPException(503, "评测 V2 持久化服务暂不可用") from exc


def _safe_slice_tags(metadata: Any) -> dict[str, str]:
    """只把脱敏后的有限分类标签写入 Run 快照，供可复现切片汇总。"""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return {}
    if not isinstance(metadata, dict):
        return {}
    clean, _changed = sanitize_candidate_payload(metadata)
    result: dict[str, str] = {}
    for field in ("category", "scenario", "risk_level", "difficulty"):
        value = clean.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        normalized = " ".join(str(value).split())
        if normalized and len(normalized) <= 64:
            result[field] = normalized
    return result


def verify_evidence_anchors(
    evidence: list[dict[str, Any]],
    actor: str,
    principal: Any,
    *,
    registry: Any = None,
    chunk_store: Any = None,
) -> list[dict[str, Any]]:
    """将审核声明与当前授权知识库中的已发布文档片段逐项核验。"""
    from backend.rag.authz import RagAuthorization

    try:
        authorization = RagAuthorization.build(principal)
    except Exception as exc:  # noqa: BLE001 — 授权错误必须 fail-closed
        raise PermissionError("无法确认当前身份的知识库访问范围") from exc
    if registry is None:
        from backend.rag.indexing.doc_registry import DocumentRegistry

        registry = DocumentRegistry()
    if chunk_store is None:
        from backend.rag.indexing.chunk_store import get_chunk_store

        chunk_store = get_chunk_store()

    verified: list[dict[str, Any]] = []
    documents_by_kb: dict[str, list[dict[str, Any]]] = {}
    chunks_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for claim in evidence:
        kb_id = str(claim.get("kb_id", ""))
        if kb_id not in authorization.kb_scope:
            raise PermissionError("当前身份无权核验此知识库证据")
        if kb_id not in documents_by_kb:
            documents_by_kb[kb_id] = registry.list_by_kb(kb_id)
        doc_id = str(claim.get("doc_id", ""))
        doc_version = str(claim.get("doc_version", ""))
        matches = [
            row for row in documents_by_kb[kb_id]
            if str(row.get("doc_id", "")) == doc_id
            and str(
                row.get("version_id")
                or row.get("active_generation")
                or row.get("index_generation")
                or ""
            ) == doc_version
            and row.get("status") == "active"
        ]
        if len(matches) != 1:
            raise ValueError("证据文档或版本已失效，无法唯一匹配当前发布版本")
        document = matches[0]
        source_hash = str(document.get("file_hash", "")).lower()
        if not re.fullmatch(r"[a-f0-9]{64}", source_hash):
            raise ValueError("证据来源文档缺少有效内容哈希")
        chunk_key = (kb_id, doc_id)
        if chunk_key not in chunks_by_doc:
            chunks_by_doc[chunk_key] = [
                row for row in chunk_store.get_by_doc_id(doc_id)
                if str(row.get("kb_id", "")) == kb_id
            ]
        requested_anchor_id = str(claim.get("anchor_id") or "")
        quote = " ".join(str(claim.get("quote", "")).split())
        if requested_anchor_id:
            anchors = [
                row for row in chunks_by_doc[chunk_key]
                if str(row.get("chunk_id", "")) == requested_anchor_id
            ]
        else:
            anchors = [
                row for row in chunks_by_doc[chunk_key]
                if quote and quote in " ".join(str(row.get("content", "")).split())
            ]
        if len(anchors) != 1:
            raise ValueError("证据原文未命中唯一当前片段，请核对原文引用")
        anchor = anchors[0]
        anchor_id = str(anchor.get("chunk_id", ""))
        content = str(anchor.get("content", ""))
        if not anchor_id or not content:
            raise ValueError("证据片段缺少稳定标识或原文")
        if requested_anchor_id and quote and quote not in " ".join(content.split()):
            raise ValueError("证据引用不属于指定原文锚点")
        anchor_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        supplied_anchor_hash = str(claim.get("anchor_text_hash", ""))
        supplied_source_hash = str(claim.get("source_content_hash", "")).lower()
        if supplied_anchor_hash and supplied_anchor_hash != anchor_hash:
            raise ValueError("证据锚点内容已变化")
        if supplied_source_hash and supplied_source_hash != source_hash:
            raise ValueError("证据来源文档已变化")
        verified.append({
            "kb_id": kb_id,
            "doc_id": doc_id,
            "doc_version": doc_version,
            "anchor_id": anchor_id,
            "anchor_text_hash": anchor_hash,
            "source_content_hash": source_hash,
            "verified_by": actor,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        })
    return verified


def _validate_import_file(filename: str, data: bytes, manifest_text: str) -> dict[str, Any]:
    from backend.evaluation.dataset import imports as legacy_imports

    if len(data) > legacy_imports.MAX_BYTES:
        raise HTTPException(413, "上传文件超过 5 MB 限制")
    try:
        manifest = json.loads(manifest_text or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "manifest 不是合法 JSON") from exc
    if isinstance(manifest, dict) and manifest.get("module") not in (None, "rag"):
        try:
            return validate_non_rag_upload(filename, data, manifest)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    result = legacy_imports.validate_upload(filename, data, manifest)
    if not result.get("valid"):
        return result
    return result


async def _validate_import_dataset_domain(identity: Identity, manifest: dict[str, Any]) -> str:
    dataset_key = str(manifest.get("dataset_key", "")).strip()
    module = str(manifest.get("module", "")).strip()
    if not dataset_key or not module:
        raise HTTPException(422, "导入 manifest 必须包含数据集标识和模块")
    try:
        domain = await asyncio.to_thread(
            repository.get_dataset_domain, identity.tenant_id, dataset_key=dataset_key,
        )
    except repository.RepositoryNotFoundError as exc:
        raise HTTPException(404, "导入目标数据集不存在或不可用") from exc
    if domain != module:
        raise HTTPException(422, "导入模块与目标数据集业务域不一致")
    return domain


def _import_candidate_content(item: dict[str, Any], module: str) -> dict[str, Any]:
    expected = item.get("expected") or {}
    if module == "rag":
        proposed_facts = [{
            "reference_answer": expected.get("reference_answer", ""),
            "required_facts": expected.get("required_facts", []),
            "behavior": expected.get("behavior", "answer"),
        }]
        proposed_evidence = expected.get("evidence", [])
    else:
        proposed_facts = [expected]
        proposed_evidence = []
    case_input = item.get("input")
    if not isinstance(case_input, dict):
        case_input = {"query": item.get("question", "")}
    elif not str(case_input.get("query", "")).strip():
        case_input = {**case_input, "query": item.get("question", "")}
    metadata = item.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    for field in ("tier", "query_type", "tags", "splits"):
        if field in item:
            metadata[field] = item[field]
    return {
        "input": case_input,
        "proposed_facts": proposed_facts,
        "proposed_evidence": proposed_evidence,
        "metadata": metadata,
    }


@router.get("/import-template")
async def download_import_template(
    module: str = Query(default="rag"),
    format: str = Query(default="jsonl", pattern="^(jsonl|csv|fields)$"),
    _identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    """下载导入模板三件套：RAG JSONL 样例 / 非 RAG CSV 样例 / 字段说明。"""
    from backend.evaluation_v2.import_templates import build_template

    try:
        filename, content, media_type = build_template(module, fmt=format)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/datasets/evidence-search")
async def search_dataset_evidence(
    body: EvidenceSearchCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """在知识库快照内检索证据切片，供建集/编辑时勾选引用。

    检索范围固定为快照 fulltext：引用只能来自快照内切片，与转正时的
    ``verify_evidence_anchors`` 同一来源约束，从源头保证引用可核验。
    """
    _role(actor, "admin")
    from backend.evaluation.corpus_snapshots import load
    from backend.evaluation_v2.evidence_search import search_evidence

    try:
        snapshot = await asyncio.to_thread(load, body.snapshot_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "知识库快照不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    fulltext = snapshot.get("fulltext") or []
    items = await asyncio.to_thread(search_evidence, fulltext, body.query, body.top_k)
    version_by_doc = {
        str(row.get("doc_id")): str(row.get("version_id") or row.get("active_generation") or row.get("index_generation") or "")
        for row in (snapshot.get("documents") or [])
        if isinstance(row, dict) and row.get("doc_id")
    }
    for item in items:
        item["doc_version"] = version_by_doc.get(item["doc_id"], "")
    return {
        "snapshot_id": body.snapshot_id,
        "kb_id": str(snapshot.get("kb_id") or ""),
        "items": items,
    }


@router.get("/datasets")
async def list_datasets(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        items = await asyncio.to_thread(
            repository.list_datasets, identity.tenant_id, limit=limit, offset=offset,
        )
        return {"items": items, "limit": limit, "offset": offset}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/datasets", status_code=201)
async def create_dataset(
    body: DatasetCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    _clean, changed = sanitize_candidate_payload(body.model_dump(mode="json"))
    if changed:
        raise HTTPException(422, "数据集名称或说明包含敏感值，请先脱敏")
    try:
        return await asyncio.to_thread(
            repository.create_dataset,
            identity.tenant_id,
            actor.actor,
            dataset_key=body.dataset_key,
            name=body.name,
            domain=body.domain,
            description=body.description,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/datasets/imports/preview")
async def preview_dataset_import(
    file: UploadFile = File(...),
    manifest: str = Form("{}"),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "editor")
    validation = _validate_import_file(file.filename or "", await file.read(5 * 1024 * 1024 + 1), manifest)
    cases = validation.get("cases", [])
    source_manifest = validation.get("manifest")
    if not isinstance(source_manifest, dict):
        try:
            source_manifest = json.loads(manifest or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "manifest 不是合法 JSON") from exc
    module = await _validate_import_dataset_domain(identity, source_manifest)
    preview = []
    for item in cases[:5]:
        clean, _changed = sanitize_candidate_payload(_import_candidate_content(item, module))
        preview.append({"id": item.get("id", ""), "candidate": clean})
    duplicates = 0
    queries = [
        str((item.get("input") or {}).get("query") or item.get("question") or "").strip()
        for item in cases
    ]
    dataset_key = str(source_manifest.get("dataset_key", "")).strip()
    if any(queries) and dataset_key:
        duplicates = await asyncio.to_thread(
            repository.count_duplicate_queries, identity.tenant_id, dataset_key, queries,
        )
    return {
        "valid": bool(validation.get("valid")),
        # 预检统计口径：case_count=解析行数（含错误行），valid_count=合法行数
        "case_count": validation.get("rows_count", validation.get("case_count", len(cases))),
        "valid_count": len(cases),
        "errors": validation.get("errors", []),
        "duplicates": duplicates,
        "preview": preview,
        "persisted": False,
    }


@router.post("/datasets/imports", status_code=202)
async def import_dataset_candidates(
    file: UploadFile = File(...),
    manifest: str = Form("{}"),
    request_id: str = Form(..., min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
    skip_errors: bool = Form(default=False),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "editor")
    data = await file.read(5 * 1024 * 1024 + 1)
    validation = _validate_import_file(file.filename or "", data, manifest)
    skipped_lines = 0
    if not validation.get("valid"):
        errors = validation.get("errors", [])
        # 行级错误才计入跳过数；file/manifest 级错误（line=0）无法按行跳过
        skipped_lines = len({
            error.get("line", 0) for error in errors
            if isinstance(error.get("line"), int) and error["line"] > 0
        })
        if not (skip_errors and skipped_lines and validation.get("cases")):
            raise HTTPException(422, {"code": "IMPORT_VALIDATION_FAILED", "errors": errors})
    source_manifest = validation.get("manifest") or json.loads(manifest or "{}")
    module = await _validate_import_dataset_domain(identity, source_manifest)
    upload_hash = hashlib.sha256(data).hexdigest()
    payloads = []
    for item in validation.get("cases", []):
        raw_payload = {
            **_import_candidate_content(item, module),
            "source_ref": {"dataset_key": source_manifest["dataset_key"], "module": module, "dataset_version": source_manifest["version"], "case_id": item.get("id", ""), "file_hash": upload_hash, "filename": file.filename or "", "request_id": request_id, "batch_case_count": len(validation.get("cases", []))},
            "source_content_hash": hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        }
        clean, _changed = sanitize_candidate_payload(raw_payload)
        payloads.append(clean)
    try:
        candidates = await asyncio.to_thread(
            repository.create_candidates_bulk,
            identity.tenant_id,
            actor.actor,
            source_kind="import",
            payloads=payloads,
            request_id=request_id,
        )
        return {
            "request_id": request_id,
            "candidate_count": len(candidates),
            "candidate_ids": [str(item["candidate_id"]) for item in candidates],
            "review_status": "in_review",
            "skipped": skipped_lines,
        }
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/candidates")
async def list_candidates(
    review_status: str | None = Query(default=None, pattern=r"^(draft|needs_evidence|in_review|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        items = await asyncio.to_thread(
            repository.list_candidates,
            identity.tenant_id,
            review_status=review_status,
            limit=limit,
            offset=offset,
        )
        return {"items": items, "limit": limit, "offset": offset}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/candidates", status_code=201)
async def create_candidate(
    body: CandidateCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "editor")
    if not str(body.payload.input.get("query", "")).strip():
        raise HTTPException(422, "候选输入必须包含非空 query")
    clean, _changed = sanitize_candidate_payload(body.payload.model_dump(mode="json"))
    privacy_status = "redacted"
    try:
        return await asyncio.to_thread(
            repository.create_candidate,
            identity.tenant_id,
            actor.actor,
            source_kind=body.source_kind,
            payload=clean,
            idempotency_key=body.idempotency_key,
            privacy_status=privacy_status,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/cases/{case_id}/latest-revision")
async def get_case_latest_revision(
    case_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.get_case_latest_revision,
            identity.tenant_id,
            str(case_id),
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/cases/{case_id}/revision-candidates", status_code=201)
async def create_case_revision_candidate(
    case_id: uuid.UUID,
    body: CaseRevisionCandidateCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    clean, changed = sanitize_candidate_payload({
        "input": {"query": body.query},
        "observed_answer": body.observed_answer,
    })
    if changed:
        raise HTTPException(422, "问题或观察答案包含敏感值，请先脱敏")
    try:
        return await asyncio.to_thread(
            repository.create_case_revision_candidate,
            identity.tenant_id,
            actor.actor,
            str(case_id),
            query=clean["input"]["query"],
            observed_answer=clean["observed_answer"],
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/cases/{case_id}/archive")
async def archive_case(
    case_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.archive_case,
            identity.tenant_id,
            actor.actor,
            str(case_id),
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/intake/from-trace", status_code=201)
async def intake_trace(
    body: TraceIntake,
    request: Request,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    from backend.app.api.routes._trace_authz import trace_visible_to
    from backend.app.api.routes.evaluation_datasets import _find_trace

    trace = _find_trace(body.trace_id)
    if trace is None:
        raise HTTPException(404, "Trace 不存在")
    trace_data = trace if isinstance(trace, dict) else vars(trace)
    if not trace_visible_to(request, trace_data, role=actor.role):
        raise HTTPException(404, "Trace 不存在")
    question = str(trace_data.get("question") or trace_data.get("input") or "").strip()
    observed = str(trace_data.get("answer_preview") or trace_data.get("answer") or "")
    if not question:
        raise HTTPException(422, "Trace 缺少可脱敏的用户问题")
    candidate_payload, _changed = sanitize_candidate_payload({
        "input": {"query": question},
        "observed_answer": observed,
        "metadata": {"source": "production_trace", "ground_truth_verified": False},
        "source_ref": {"trace_id": body.trace_id},
    })
    try:
        result = await asyncio.to_thread(
            repository.create_candidate,
            identity.tenant_id,
            actor.actor,
            source_kind="trace",
            payload=candidate_payload,
            idempotency_key=body.idempotency_key,
            privacy_status="redacted",
        )
        return result
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: uuid.UUID,
    body: CandidateReview,
    request: Request,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    expected = body.expected
    evidence_claims = [item.model_dump(mode="json") for item in body.evidence]
    clean_claims, evidence_changed = sanitize_candidate_payload(evidence_claims)
    if expected is not None:
        expected, expected_changed = sanitize_candidate_payload(expected)
        if expected_changed:
            raise HTTPException(422, "标准事实包含敏感字段，请先完成脱敏")
    clean_rationale, rationale_changed = sanitize_candidate_payload(body.rationale)
    if evidence_changed or rationale_changed:
        raise HTTPException(422, "审核理由或证据包含敏感字段，请先完成脱敏")
    try:
        context = await asyncio.to_thread(
            repository.get_candidate_review_context,
            identity.tenant_id,
            str(candidate_id),
        )
        domain = context["domain"]
        evidence = []
        if body.verdict == "approved":
            if domain == "rag":
                behavior = str((expected or {}).get("behavior", "answer"))
                if behavior == "answer" and not clean_claims:
                    raise HTTPException(422, "回答样本必须绑定经核验的原文证据")
                if behavior == "refuse":
                    if (expected or {}).get("reason") not in {"no_evidence", "permission_denied"}:
                        raise HTTPException(422, "拒答样本必须指定 no_evidence 或 permission_denied")
                    if clean_claims or (expected or {}).get("reference_answer") or (expected or {}).get("required_facts"):
                        raise HTTPException(422, "拒答样本不能携带答案、必须事实或原文证据")
                elif behavior != "answer":
                    raise HTTPException(422, "样本 behavior 仅支持 answer 或 refuse")
                from backend.app.api.identity import resolve_principal

                evidence = await asyncio.to_thread(
                    verify_evidence_anchors,
                    clean_claims,
                    actor.actor,
                    resolve_principal(request),
                )
            else:
                if clean_claims:
                    raise HTTPException(422, "非 RAG 样本不能绑定知识库证据")
                await asyncio.to_thread(
                    validate_non_rag_case,
                    domain,
                    context["payload"].get("input"),
                    expected,
                    [],
                )
        return await asyncio.to_thread(
            repository.review_candidate,
            identity.tenant_id,
            actor.actor,
            str(candidate_id),
            expected_revision=body.expected_revision,
            verdict=body.verdict,
            rationale=clean_rationale,
            expected=expected,
            evidence=evidence,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/datasets/{dataset_id}/versions", status_code=201)
async def publish_dataset_version(
    dataset_id: uuid.UUID,
    body: DatasetPublish,
    request: Request,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        selected = [(str(item.case_id), item.revision) for item in body.items]
        dataset_domain = await asyncio.to_thread(
            repository.get_dataset_domain,
            identity.tenant_id,
            dataset_id=str(dataset_id),
        )
        evidence_rows = await asyncio.to_thread(
            repository.list_case_evidence, identity.tenant_id, selected,
        )
        claims: list[dict[str, Any]] = []
        for row in evidence_rows:
            evidence = row.get("evidence_json") or []
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            if dataset_domain != "rag":
                if evidence:
                    raise ValueError("非 RAG 样本不能绑定知识库证据")
                continue
            expected = row.get("expected_json") or {}
            if isinstance(expected, str):
                expected = json.loads(expected)
            if not isinstance(expected, dict):
                raise ValueError("样本标准事实格式无效，不能发布")
            if expected.get("behavior", "answer") == "refuse":
                if evidence:
                    raise ValueError("拒答样本不能绑定原文证据")
                continue
            if expected.get("behavior", "answer") != "answer":
                raise ValueError("样本 behavior 仅支持 answer 或 refuse")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("回答样本证据缺失或已过期，不能发布")
            claims.extend(evidence)
            if len(claims) > 20_000:
                raise ValueError("单次发布需要重新核验的证据锚点超过 20000 条")
        if len(evidence_rows) != len(selected):
            raise repository.RepositoryNotFoundError("所选样本或修订不存在")
        from backend.app.api.identity import resolve_principal

        if claims:
            await asyncio.to_thread(
                verify_evidence_anchors,
                claims,
                actor.actor,
                resolve_principal(request),
            )
        return await asyncio.to_thread(
            repository.publish_dataset_version,
            identity.tenant_id,
            actor.actor,
            str(dataset_id),
            version=body.version,
            case_ids=selected,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions(
    dataset_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        items = await asyncio.to_thread(
            repository.list_dataset_versions, identity.tenant_id, str(dataset_id),
        )
        return {"items": items}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/datasets/{dataset_id}/versions/{dataset_version_id}/export")
async def export_dataset_version(
    dataset_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    request: Request,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        exported = await asyncio.to_thread(
            repository.load_dataset_version_export,
            identity.tenant_id,
            str(dataset_id),
            str(dataset_version_id),
        )
        evidence = [
            claim
            for case in exported["cases"]
            for claim in (case.get("evidence") or [])
        ]
        if evidence:
            from backend.app.api.identity import resolve_principal

            try:
                await asyncio.to_thread(
                    verify_evidence_anchors,
                    evidence,
                    actor.actor,
                    resolve_principal(request),
                )
            except ValueError as exc:
                raise HTTPException(
                    409,
                    detail={
                        "code": "EVIDENCE_STALE",
                        "message": "当前知识库证据已变化，Golden 导出已拒绝。",
                    },
                ) from exc

        manifest = {
            "type": "dataset_manifest",
            "schema_version": "evaluation.dataset.v2",
            "dataset": {
                "dataset_id": exported["dataset_id"],
                "dataset_key": exported["dataset_key"],
                "name": exported["dataset_name"],
                "domain": exported["domain"],
            },
            "version": exported["version"],
            "content_hash": exported["content_hash"],
            "published_by": exported["published_by"],
            "published_at": exported["published_at"].isoformat(),
            "case_count": len(exported["cases"]),
        }
        lines = [json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))]
        lines.extend(
            json.dumps(
                {"type": "case", **case},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for case in exported["cases"]
        )
        content = ("\n".join(lines) + "\n").encode("utf-8")
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                413,
                detail={
                    "code": "EXPORT_TOO_LARGE",
                    "message": "Golden JSONL 导出超过 10 MB 限制，请拆分数据集版本。",
                },
            )

        await asyncio.to_thread(
            repository.record_dataset_version_export,
            identity.tenant_id,
            actor.actor,
            str(dataset_version_id),
            content_hash_value=exported["content_hash"],
            case_count=len(exported["cases"]),
        )
        filename = f"{exported['dataset_key']}-{exported['version']}.jsonl"
        return StreamingResponse(
            iter([content]),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/datasets/{dataset_id}/versions/{dataset_version_id}/coverage")
async def get_dataset_version_coverage(
    dataset_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        snapshot = await asyncio.to_thread(
            repository.get_dataset_version_coverage,
            identity.tenant_id,
            str(dataset_id),
            str(dataset_version_id),
        )
        dimensions: dict[str, dict[str, Any]] = {}
        fields = ("category", "scenario", "risk_level", "difficulty")
        total = int(snapshot["case_count"])
        for field in fields:
            counts: Counter[str] = Counter()
            for case in snapshot["cases"]:
                label = _safe_slice_tags({field: case.get(field)}).get(field, "未标注")
                counts[label] += 1
            covered = total - counts.get("未标注", 0)
            ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            visible = ordered[:100]
            dimensions[field] = {
                "covered_count": covered,
                "coverage_ratio": round(covered * 100 / total, 1) if total else 0,
                "items": [
                    {
                        "label": label,
                        "count": count,
                        "ratio": round(count * 100 / total, 1) if total else 0,
                    }
                    for label, count in visible
                ],
                "other_count": sum(count for _label, count in ordered[100:]),
            }
        snapshot.pop("cases", None)
        snapshot["dimensions"] = dimensions
        return snapshot
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/datasets/{dataset_id}/versions/compare")
async def compare_dataset_versions(
    dataset_id: uuid.UUID,
    request: Request,
    base_version_id: uuid.UUID = Query(...),
    target_version_id: uuid.UUID = Query(...),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        base_id = str(base_version_id)
        target_id = str(target_version_id)
        comparison = await asyncio.to_thread(
            repository.compare_dataset_versions,
            identity.tenant_id,
            str(dataset_id),
            base_id,
            target_id,
        )
        evidence_by_version = await asyncio.to_thread(
            repository.list_dataset_version_evidence,
            identity.tenant_id,
            str(dataset_id),
            [base_id, target_id],
        )
        stale: dict[str, list[str]] = {}
        rows_by_version = {
            version_id: [
                item for item in evidence_by_version[version_id] if item["evidence"]
            ]
            for version_id in (base_id, target_id)
        }
        if any(rows_by_version.values()):
            from backend.app.api.identity import resolve_principal
            from backend.rag.indexing.chunk_store import get_chunk_store
            from backend.rag.indexing.doc_registry import DocumentRegistry

            principal = resolve_principal(request)
            registry = DocumentRegistry()
            chunk_store = get_chunk_store()
        for version_id in (base_id, target_id):
            rows = rows_by_version[version_id]
            stale_ids: list[str] = []

            def check_batch(batch: list[dict[str, Any]]) -> None:
                claims = [claim for item in batch for claim in item["evidence"]]
                try:
                    verify_evidence_anchors(
                        claims, actor.actor, principal,
                        registry=registry, chunk_store=chunk_store,
                    )
                except ValueError:
                    if len(batch) == 1:
                        stale_ids.append(batch[0]["case_id"])
                        return
                    midpoint = len(batch) // 2
                    check_batch(batch[:midpoint])
                    check_batch(batch[midpoint:])

            for start in range(0, len(rows), 128):
                check_batch(rows[start:start + 128])
            stale[version_id] = sorted(set(stale_ids))

        comparison["stale"] = {
            "base": stale[base_id],
            "target": stale[target_id],
        }
        return comparison
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/evaluators")
async def list_evaluator_versions(
    identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        items = await asyncio.to_thread(
            repository.list_evaluator_versions, identity.tenant_id,
        )
        return {"items": items}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/evaluators", status_code=201)
async def create_evaluator_version(
    body: EvaluatorCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    _config, changed = sanitize_candidate_payload(body.config)
    if changed:
        raise HTTPException(422, "评分器配置包含疑似密钥或个人信息，请先脱敏")
    try:
        return await asyncio.to_thread(
            repository.create_evaluator_version,
            identity.tenant_id,
            actor.actor,
            evaluator_key=body.evaluator_key,
            version=body.version,
            evaluator_kind=body.evaluator_kind,
            config=body.config,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/evaluator-versions/{evaluator_version_id}/publish")
async def publish_evaluator_version(
    evaluator_version_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.publish_evaluator_version,
            identity.tenant_id,
            actor.actor,
            str(evaluator_version_id),
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/suites")
async def list_suites(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(require_product_identity),
    _actor: OperatorIdentity = Depends(require_user_actor),
):
    try:
        items = await asyncio.to_thread(
            repository.list_suites, identity.tenant_id, limit=limit, offset=offset,
        )
        mapped: list[dict[str, Any]] = []
        for item in items:
            selection = item.get("case_selection") or {}
            manifest = item.get("dataset_manifest") or {}
            if isinstance(selection, str):
                import json

                selection = json.loads(selection)
            if isinstance(manifest, str):
                import json

                manifest = json.loads(manifest)
            frozen_items = manifest.get("items", []) if isinstance(manifest, dict) else []
            selected_ids = selection.get("case_ids") if isinstance(selection, dict) else None
            case_ids = selected_ids or [str(case.get("case_id", "")) for case in frozen_items]
            policy = item.get("metric_policy") or {}
            if isinstance(policy, str):
                import json

                policy = json.loads(policy)
            mapped.append({
                "suite_id": item.get("suite_id"),
                "suite_version_id": item.get("suite_version_id"),
                "status": "published" if item.get("suite_version_id") else "draft",
                "name": item.get("name", item.get("suite_key", "")),
                "suite_key": item.get("suite_key", ""),
                "module": item.get("module", ""),
                "version": item.get("suite_version", ""),
                "dataset_version": item.get("dataset_version", ""),
                "dataset_id": item.get("dataset_id"),
                "dataset_hash": item.get("dataset_hash", ""),
                "case_ids": case_ids,
                "case_count": len(case_ids),
                "kb_id": "",
                "fixture_set": "",
                "trigger_classes": [],
                "thresholds": policy.get("thresholds", policy) if isinstance(policy, dict) else {},
                "prompt_mapping": item.get("evaluator_versions") or {},
                "tool_mapping": {},
                "metric_policy": policy,
                "security_policy": item.get("security_policy") or {},
                "source": "evaluation_v2",
                "read_only": True,
            })
        items = mapped
        if identity.tenant_id == "default":
            # 静态旧 Suite 仅作为迁移 adapter 只读暴露，不进入 V2 发布或运行决策。
            from backend.app.api.routes.evaluation_datasets import _suite_items

            legacy = [
                {
                    **item,
                    "source": "legacy_unverified",
                    "read_only": True,
                    "status": "legacy_unverified",
                }
                for item in _suite_items()
            ]
            items = [*items, *legacy]
        return {"items": items, "limit": limit, "offset": offset}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/suites", status_code=201)
async def create_suite(
    body: SuiteCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    require_supported_runner_module(body.module)
    _clean, changed = sanitize_candidate_payload(body.model_dump(mode="json"))
    if changed:
        raise HTTPException(422, "评测方案配置包含疑似密钥或个人信息，请先脱敏")
    try:
        return await asyncio.to_thread(
            repository.create_suite,
            identity.tenant_id,
            actor.actor,
            suite_key=body.suite_key,
            name=body.name,
            module=body.module,
            dataset_version_id=str(body.dataset_version_id),
            version=body.version,
            case_selection=body.case_selection,
            evaluator_versions=[
                item.model_dump(mode="json") for item in body.evaluator_versions
            ],
            metric_policy=body.metric_policy,
            security_policy=body.security_policy,
            minimum_valid_samples=body.minimum_valid_samples,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/suites/{suite_id}/versions", status_code=201)
async def append_suite_version(
    suite_id: uuid.UUID,
    body: SuiteVersionCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    _config, changed = sanitize_candidate_payload(body.model_dump(mode="json"))
    if changed:
        raise HTTPException(422, "评测方案配置包含疑似密钥或个人信息，请先脱敏")
    try:
        return await asyncio.to_thread(
            repository.append_suite_version,
            identity.tenant_id,
            actor.actor,
            str(suite_id),
            dataset_version_id=str(body.dataset_version_id),
            version=body.version,
            case_selection=body.case_selection,
            evaluator_versions=[
                item.model_dump(mode="json") for item in body.evaluator_versions
            ],
            metric_policy=body.metric_policy,
            security_policy=body.security_policy,
            minimum_valid_samples=body.minimum_valid_samples,
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/suite-versions/{suite_version_id}/publish")
async def publish_suite_version(
    suite_version_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.publish_suite_version,
            identity.tenant_id,
            actor.actor,
            str(suite_version_id),
        )
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(repository.list_runs, identity.tenant_id, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/runs", status_code=202)
async def create_eval_run(
    body: EvalRunCreate,
    identity: Identity = Depends(require_product_identity),
    principal: Any = Depends(require_principal),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """冻结已发布 Suite 输入，并投递现有可恢复评测 Worker。"""
    _role(actor, "super_admin")
    if identity.tenant_id != "default":
        raise HTTPException(403, "现有 Runner 与任务系统仅支持 default 租户，其他租户尚未迁移")
    try:
        definition = await asyncio.to_thread(repository.get_run_definition, identity.tenant_id, str(body.suite_version_id))
        module = str(definition["module"])
        is_rag = module == "rag"
        if is_rag:
            if not body.kb_id:
                raise HTTPException(422, "RAG 评测必须指定知识库")
            from backend.rag.authz import RagAuthorization

            authorization = RagAuthorization.build(principal)
            if not authorization.can_search_kb(body.kb_id):
                raise HTTPException(403, "当前身份无权运行此知识库评测")
        elif body.kb_id is not None or body.subject_user_id is not None:
            raise HTTPException(422, "非 RAG 评测不接受知识库或测试用户范围")

        # 保留 RAG 既有指纹字段，避免部署后重试历史幂等键被误判冲突。
        request_fingerprint = repository.content_hash({
            "suite_version_id": str(body.suite_version_id),
            "kb_id": body.kb_id,
            "subject_user_id": body.subject_user_id,
            "prompt_versions": body.prompt_versions,
        })
        existing = await asyncio.to_thread(repository.get_run_by_request, identity.tenant_id, body.request_id)
        if existing:
            snapshot = existing.get("snapshot_json") or {}
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)
            if snapshot.get("request_fingerprint") != request_fingerprint:
                raise HTTPException(409, "幂等键已用于不同的评测请求")
            from backend.evaluation.managed_tasks import submit_v2_run

            task = await asyncio.to_thread(submit_v2_run, str(existing["run_id"]), actor.actor)
            run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(existing["run_id"]))
            return {"run": run, "task": task, "reused": True}

        evaluator_versions = definition.get("evaluator_versions") or []
        evaluator_definitions = definition.get("evaluator_definitions") or []
        evaluator_snapshot = _build_run_evaluator_snapshot(
            evaluator_versions, evaluator_definitions, module=module,
        )
        if is_rag:
            permission_cases = []
            for case in definition["cases"]:
                expected = case.get("expected_json") or {}
                if isinstance(expected, str):
                    expected = json.loads(expected)
                if expected.get("behavior") == "refuse" and expected.get("reason") == "permission_denied":
                    permission_cases.append(case)
            if permission_cases and body.subject_user_id is None:
                raise ValueError("权限拒答样本需要指定服务端可解析的测试用户")
            if body.subject_user_id is not None:
                from backend.security.task_authorization import resolve_task_authorization

                subject_authorization = resolve_task_authorization(str(body.subject_user_id), identity.tenant_id)
                if permission_cases and subject_authorization.can_search_kb(body.kb_id):
                    raise ValueError("权限拒答样本的测试用户当前可访问目标知识库")

            from backend.evaluation.corpus_snapshots import capture
            from backend.evaluation.managed_execution import validate_kb

            validate_kb(body.kb_id)
            snapshot = await asyncio.to_thread(capture, body.kb_id, actor.actor)
            doc_versions = {
                (
                    str(row.get("doc_id", "")),
                    str(row.get("version_id") or row.get("active_generation") or row.get("index_generation") or ""),
                ): row
                for row in snapshot.get("documents", [])
            }
            for case in definition["cases"]:
                evidence = case.get("evidence_json") or []
                if isinstance(evidence, str):
                    evidence = json.loads(evidence)
                expected = case.get("expected_json") or {}
                if isinstance(expected, str):
                    expected = json.loads(expected)
                if expected.get("behavior") == "answer" and not evidence:
                    raise ValueError("回答题缺少审核证据")
                for anchor in evidence:
                    if str(anchor.get("kb_id", "")) != body.kb_id:
                        raise PermissionError("样本证据不属于本次评测知识库")
                    document = doc_versions.get((str(anchor.get("doc_id", "")), str(anchor.get("doc_version", ""))))
                    if not document or str(document.get("file_hash", "")).lower() != str(anchor.get("source_content_hash", "")).lower():
                        raise ValueError("Golden 证据版本已不在当前可发布语料中，请重新审核并发布样本")
        else:
            snapshot = None

        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"evaluation-v2:{identity.tenant_id}:{actor.actor}:{body.request_id}"))
        corpus_snapshot_id = str(uuid.uuid5(uuid.UUID(run_id), "corpus-snapshot")) if is_rag else None
        case_refs = [
            {
                "case_id": str(item["case_id"]),
                "case_revision": int(item["case_revision"]),
                "content_hash": str(item["revision_hash"]),
                "slice_tags": _safe_slice_tags(item.get("metadata_json")),
            }
            for item in definition["cases"]
        ]
        if is_rag:
            permissions_digest = repository.content_hash({
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
                "roles": list(principal.roles),
                "permissions": list(principal.permissions or ()),
                "kb_id": body.kb_id,
            })
            from backend.evaluation_v2.runtime_environment import current_runtime_environment
            from backend.infra.llm.registry_store import refresh_registry

            # 冻结指纹前与 Worker 执行侧同基准刷新注册表：Worker 执行前也会
            # refresh，两侧都解析最新全局角色绑定，避免请求上下文的注册表
            # 缓存与执行侧漂移造成必然 INVALID。
            await refresh_registry()
            runtime_environment = await asyncio.to_thread(current_runtime_environment)
            environment = {
                "runner": "legacy-rag-v1",
                "evaluation_mode": body.evaluation_mode,
                "suite_hash": str(definition["suite_hash"]),
                "dataset_hash": str(definition["dataset_hash"]),
                "corpus_hash": str(snapshot["content_hash"]),
                "evaluator_versions": evaluator_versions,
                "prompt_versions": body.prompt_versions,
                "evaluator_definitions": evaluator_snapshot,
                "runtime_environment": runtime_environment,
            }
            execution_semantics = "system_quality"
        else:
            from backend.evaluation_v2.capabilities import (
                CONTRACT_ONLY_RUNNER_MODULES,
            )

            permissions_digest = None
            execution_semantics = "contract_only" if module in CONTRACT_ONLY_RUNNER_MODULES else "system_quality"
            environment = {
                "runner": f"legacy-{module}-v1",
                "module": module,
                "evaluation_mode": body.evaluation_mode,
                "execution_semantics": execution_semantics,
                "suite_hash": str(definition["suite_hash"]),
                "dataset_hash": str(definition["dataset_hash"]),
                "prompt_versions": body.prompt_versions,
            }
            runtime_environment = None

        snapshot_json = {
            "schema_version": "evaluation.run.v2",
            "source": body.source,
            "evaluation_mode": body.evaluation_mode,
            "request_fingerprint": request_fingerprint,
            "runner": "legacy-rag-v1" if is_rag else f"legacy-{module}-v1",
            "module": module,
            "execution_semantics": execution_semantics,
            "suite_version_id": str(body.suite_version_id),
            "suite_hash": str(definition["suite_hash"]),
            "dataset_version_id": str(definition["dataset_version_id"]),
            "dataset_hash": str(definition["dataset_hash"]),
            **({
                "kb_id": body.kb_id,
                "corpus_snapshot_id": corpus_snapshot_id,
                "legacy_snapshot_id": snapshot["snapshot_id"],
                "corpus_hash": snapshot["content_hash"],
            } if is_rag else {}),
            "case_count": len(case_refs),
            "cases": case_refs,
            "evaluator_versions": evaluator_versions,
            "evaluator_definitions": evaluator_snapshot,
            "prompt_versions": body.prompt_versions,
            **({"runtime_environment": runtime_environment} if runtime_environment is not None else {}),
            "environment": environment,
            "metric_policy": definition.get("metric_policy") or {},
            "security_policy": definition.get("security_policy") or {},
            "minimum_valid_samples": int(definition["minimum_valid_samples"]),
            "subject_user_id": body.subject_user_id,
            "cost_status": "not_reported_by_legacy_runner" if is_rag else "not_applicable_no_model_call",
        }
        run = await asyncio.to_thread(
            repository.create_run,
            identity.tenant_id,
            actor.actor,
            run_id=run_id,
            request_id=body.request_id,
            suite_version_id=str(body.suite_version_id),
            kb_id=body.kb_id,
            legacy_snapshot=snapshot,
            permissions_digest=permissions_digest,
            snapshot_json=snapshot_json,
            env_hash=repository.content_hash(environment),
            request_fingerprint=request_fingerprint,
        )
        from backend.evaluation.managed_tasks import submit_v2_run

        task = await asyncio.to_thread(submit_v2_run, str(run["run_id"]), actor.actor)
        run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run["run_id"]))
        return {"run": run, "task": task, "reused": False}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/runs/{run_id}")
async def get_eval_run(
    run_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run_id))
        return {"run": run, "fingerprint": _run_fingerprint(run.get("snapshot_json"))}
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


def _run_fingerprint(snapshot_json: Any) -> dict[str, Any]:
    """从运行快照提取前端可读的环境指纹；缺字段返回空值而非报错。"""
    snapshot = snapshot_json
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    if not isinstance(snapshot, dict):
        return {}
    models = (snapshot.get("runtime_environment") or {}).get("models") or {}
    if not isinstance(models, dict):
        models = {}

    def _model_name(role: str) -> str:
        entry = models.get(role)
        return str(entry.get("model", "")) if isinstance(entry, dict) else ""

    return {
        "source": str(snapshot.get("source") or "manual"),
        "evaluation_mode": str(snapshot.get("evaluation_mode") or ""),
        "runner": str(snapshot.get("runner") or ""),
        "module": str(snapshot.get("module") or ""),
        "dataset_version_id": str(snapshot.get("dataset_version_id") or ""),
        "prompt_versions": snapshot.get("prompt_versions") or {},
        "evaluator_versions": snapshot.get("evaluator_versions") or [],
        "generation_model": _model_name("generation"),
        "embedding_model": _model_name("embedding"),
        "rerank_model": _model_name("rerank"),
        "corpus_snapshot_id": str(snapshot.get("corpus_snapshot_id") or ""),
        "dataset_hash": str(snapshot.get("dataset_hash") or ""),
        "corpus_hash": str(snapshot.get("corpus_hash") or ""),
    }


@router.get("/runs/{run_id}/results")
async def get_eval_run_results(
    run_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        rows = await asyncio.to_thread(
            repository.list_run_results,
            identity.tenant_id,
            str(run_id),
            limit=limit,
            offset=offset,
        )
        return [
            {
                "result_id": row["result_id"],
                "case_id": row["case_id"],
                "case_revision": row["case_revision"],
                "status": row["result_status"],
                "scores": row["scores_json"],
                "failure_stage": row["failure_stage"],
                "latency_ms": row["latency_ms"],
                "cost_amount": row["cost_amount"],
                "cost_currency": row["cost_currency"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/runs/{run_id}/results/{case_id}")
async def get_eval_run_case_detail(
    run_id: uuid.UUID,
    case_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """读取逐题详情时复核 V2 结果索引，并在展示出口脱敏。"""
    _role(actor, "super_admin")
    try:
        stored = await asyncio.to_thread(
            repository.get_run_result,
            identity.tenant_id,
            str(run_id),
            str(case_id),
        )
        from backend.evaluation.export_masking import mask_result_for_viewer
        from backend.evaluation.storage import load_report
        from backend.evaluation_v2.privacy import sanitize_candidate_payload
        from backend.shared.jsonable import safe_jsonable

        report, _meta = await asyncio.to_thread(load_report, str(run_id))
        item = next((row for row in report.results if row.case_id == str(case_id)), None)
        if item is None or item.status != stored["result_status"]:
            raise RuntimeError("持久化逐例索引与结果产物不一致")

        masked = mask_result_for_viewer(item.model_dump(mode="json"))
        safe_result, _changed = sanitize_candidate_payload(masked)
        return {
            "run_id": str(run_id),
            "case_id": str(case_id),
            "case_revision": stored["case_revision"],
            "result": safe_jsonable(safe_result),
            "trace_id": stored["trace_id"],
            "latency_ms": stored["latency_ms"],
            "cost_amount": stored["cost_amount"],
            "cost_currency": stored["cost_currency"],
            "cost_status": "available" if stored["cost_amount"] is not None else "not_reported_by_legacy_runner",
        }
    except FileNotFoundError as exc:
        raise HTTPException(503, "评测逐题产物暂不可用") from exc
    except Exception as exc:  # noqa: BLE001 — 不把文件路径或内部产物错误返回客户端
        _safe_error(exc)


@router.get("/runs/{run_id}/results/{case_id}/human-reviews")
async def get_eval_human_reviews(
    run_id: uuid.UUID,
    case_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        return await asyncio.to_thread(
            repository.list_human_reviews,
            identity.tenant_id,
            str(run_id),
            str(case_id),
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/runs/{run_id}/results/{case_id}/human-reviews", status_code=201)
async def create_eval_human_review(
    run_id: uuid.UUID,
    case_id: uuid.UUID,
    body: HumanReviewCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        return await asyncio.to_thread(
            repository.record_human_review,
            identity.tenant_id,
            actor.actor,
            str(run_id),
            str(case_id),
            evaluator_version_id=str(body.evaluator_version_id),
            request_id=body.request_id,
            label=body.label,
            scores=body.scores,
            rationale=body.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/runs/{run_id}/cancel")
async def cancel_eval_run(
    run_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run_id))
        if run["job_status"] in {"completed", "failed", "cancelled", "blocked"}:
            return {"run_id": str(run_id), "accepted": False, "job_status": run["job_status"]}
        from backend.services import task_service
        from backend.tasks.task_manager import cancel_task

        task = await asyncio.to_thread(task_service.get_task, str(run_id))
        if not task or task.graph_name != "rag_eval" or task.tenant_id != identity.tenant_id:
            raise repository.RepositoryNotFoundError("评测运行不存在")
        result = await asyncio.to_thread(cancel_task, str(run_id))
        if not result.get("ok"):
            raise HTTPException(503, "取消请求未送达，请稍后重试")
        refreshed_task = await asyncio.to_thread(task_service.get_task, str(run_id))
        if refreshed_task and refreshed_task.status.value == "CANCELLED":
            await asyncio.to_thread(
                repository.terminate_run_with_gate,
                identity.tenant_id,
                actor.actor,
                str(run_id),
                status="cancelled",
                invalid_reason="cancelled_before_execution",
                policy_hash=repository.content_hash({"terminal": "cancelled_before_execution"}),
                verdict="INVALID",
                reasons=[{"code": "cancelled_before_execution"}],
            )
        current = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run_id))
        return {"run_id": str(run_id), "accepted": True, "job_status": current["job_status"], "task": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_eval_run(
    run_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run_id))
        if run["job_status"] not in {"failed"}:
            raise repository.RepositoryConflictError("只有失败的 V2 运行可以显式重试")
        from backend.services import task_service
        from backend.tasks.task_manager import resume_task

        task = await asyncio.to_thread(task_service.get_task, str(run_id))
        if not task or task.graph_name != "rag_eval" or task.tenant_id != identity.tenant_id:
            raise repository.RepositoryNotFoundError("评测运行任务不存在")
        await asyncio.to_thread(
            resume_task,
            str(run_id),
            allow_failed=True,
            dispatch_type="admin_retry",
        )
        refreshed = await asyncio.to_thread(task_service.get_task, str(run_id))
        from backend.evaluation.managed_tasks import task_view

        return {"run_id": str(run_id), "task": task_view(refreshed)}
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/experiments", status_code=201)
async def create_experiment(
    body: ExperimentCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.create_experiment,
            identity.tenant_id,
            actor.actor,
            suite_version_id=str(body.suite_version_id),
            name=body.name,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/experiments")
async def list_experiments(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.list_experiments,
            identity.tenant_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/experiments/{experiment_id}/runs")
async def attach_experiment_run(
    experiment_id: uuid.UUID,
    body: ExperimentRunAttach,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.attach_experiment_run,
            identity.tenant_id,
            actor.actor,
            str(experiment_id),
            run_id=str(body.run_id),
            slot=body.slot,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/experiments/compare")
async def compare_eval_runs(
    baseline_run_id: uuid.UUID,
    candidate_run_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.compare_runs,
            identity.tenant_id,
            str(baseline_run_id),
            str(candidate_run_id),
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/gates/{run_id}")
async def get_eval_gate(
    run_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        run = await asyncio.to_thread(repository.get_run, identity.tenant_id, str(run_id))
        decisions = await asyncio.to_thread(repository.list_gate_decisions, identity.tenant_id, str(run_id))
        approvals = await asyncio.to_thread(repository.list_gate_approvals, identity.tenant_id, str(run_id))
        return {
            "run_id": str(run_id),
            "job_status": run["job_status"],
            "verdict": run["gate_verdict"],
            "reasons": run["gate_reason"],
            "decisions": decisions,
            "approvals": approvals,
        }
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.post("/gates/{run_id}/approvals", status_code=201)
async def create_eval_gate_approval(
    run_id: uuid.UUID,
    body: GateApprovalCreate,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "super_admin")
    try:
        return await asyncio.to_thread(
            repository.record_gate_approval,
            identity.tenant_id,
            actor.actor,
            str(run_id),
            gate_decision_id=str(body.gate_decision_id),
            request_id=body.request_id,
            decision=body.decision,
            release_reference=body.release_reference,
            rationale=body.rationale,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


@router.get("/audit-events")
async def get_eval_audit_events(
    target_type: str = Query("", max_length=48),
    target_id: str = Query("", max_length=128),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.list_audit_events,
            identity.tenant_id,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


__all__ = ["router"]
