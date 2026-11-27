# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

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
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

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
from backend.evaluation_v2.review_quality import (
    RagasQualityCheck,
    resolve_fact_evidence_bindings,
    validate_ragas_candidate_review,
)


from . import base
from .base import *
from .base import (
    _role,
    _validate_import_file,
    _validate_import_dataset_domain,
    _import_candidate_content,
    _load_unanswered_entry,
    verify_evidence_anchors,
    _safe_error,
    _safe_slice_tags,
)
from .schemas import *

def _current_verify_evidence_anchors(*args: Any, **kwargs: Any) -> Any:
    fn = getattr(facade, "verify_evidence_anchors", None) or verify_evidence_anchors
    return fn(*args, **kwargs)

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
    principal=Depends(require_principal),
):
    """在知识库快照内检索证据切片，供建集/编辑时勾选引用。

    检索范围固定为快照 fulltext：引用只能来自快照内切片，与转正时的
    ``verify_evidence_anchors`` 同一来源约束，从源头保证引用可核验。
    """
    _role(actor, "admin")
    _load_fn = (
        getattr(facade, "load", None)
        if facade and getattr(facade, "load", None) is not None
        else getattr(base, "load", None)
    )
    if _load_fn is None:
        from backend.evaluation.corpus_snapshots import load as _load_fn
    from backend.evaluation_v2.evidence_search import search_evidence

    try:
        snapshot = await asyncio.to_thread(_load_fn, body.snapshot_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "知识库快照不存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # 审计 #10：快照 ID 是全局 24-hex 标识，load 不做授权。捕获端只允许
    # super_admin 对可见 KB 建快照，检索端必须做同等的 KB 授权复核，
    # 否则普通 admin 可借 run 里的 legacy_snapshot_id 读到 test KB 语料。
    from backend.rag.authz import RagAuthorization, RagAuthorizationError

    snapshot_kb = str(snapshot.get("kb_id") or "")
    snapshot_tenant = str(snapshot.get("tenant_id") or "")
    try:
        authorization = await asyncio.to_thread(RagAuthorization.build, principal)
    except RagAuthorizationError as exc:
        raise HTTPException(403, "授权服务暂不可用，已拒绝操作") from exc
    if snapshot_tenant and snapshot_tenant != principal.tenant_id:
        raise HTTPException(403, "没有该知识库快照的检索权限")
    if not snapshot_kb or not authorization.can_search_kb(snapshot_kb):
        raise HTTPException(403, "没有该知识库快照的检索权限")
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
    dataset_key: str | None = Query(default=None, min_length=3, max_length=64),
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
            dataset_key=dataset_key,
            limit=limit,
            offset=offset,
        )
        return {"items": items, "limit": limit, "offset": offset}
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.get("/candidates/{candidate_id}/reviews")
async def list_candidate_reviews(
    candidate_id: uuid.UUID,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """读取审核历史；仓储查询按当前租户隔离候选和记录。"""
    _role(actor, "admin")
    try:
        items = await asyncio.to_thread(
            repository.list_candidate_annotations,
            identity.tenant_id,
            str(candidate_id),
        )
        return {"items": items}
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
        detail = await asyncio.to_thread(
            repository.get_case_latest_revision,
            identity.tenant_id,
            str(case_id),
        )
        # 批次 6a：golden 标注独立表，读侧合并进单 case 详情
        golden_map = await asyncio.to_thread(
            repository.latest_golden_map,
            identity.tenant_id,
            [str(case_id)],
        )
        detail["golden"] = golden_map.get(str(case_id), {}).get("golden")
        return detail
    except Exception as exc:  # noqa: BLE001 — API 输出不泄漏数据库细节
        _safe_error(exc)


@router.post("/cases/{case_id}/golden")
async def set_case_golden(
    case_id: uuid.UUID,
    body: CaseGoldenMark,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """Golden 标注（批次 6a）：append-only 留历史；幂等同值不追加。"""
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.set_case_golden,
            identity.tenant_id,
            actor.actor,
            str(case_id),
            golden=body.golden,
            reason=body.reason,
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


@router.post("/intake/from-unanswered", status_code=201)
async def intake_unanswered(
    body: UnansweredIntake,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """持久化坏例回流（TASK §10 P8）：ai.unanswered_questions → 审核候选。

    幂等三层：idempotency_key（create_candidate 原生，同 key 同内容重放返回
    原候选）、source_ref.origin 查重（同一条坏例跨请求只回流一次，命中返回
    200 + duplicate=true 不再创建）、审核层既有防重查询。"""
    _role(actor, "admin")
    entry = await asyncio.to_thread(_load_unanswered_entry, body.entry_id)
    if entry is None:
        raise HTTPException(404, "未答问题条目不存在")
    question = str(entry["question"] or "").strip()
    if not question:
        raise HTTPException(422, "未答问题缺少可脱敏的用户问题")
    origin = str(entry["id"])
    existing = await asyncio.to_thread(
        repository.find_candidate_by_source_origin, identity.tenant_id, origin)
    if existing is not None:
        return {**existing, "duplicate": True}
    candidate_payload, _changed = sanitize_candidate_payload({
        "input": {"query": question},
        "observed_answer": "",
        "metadata": {"source": "unanswered_question", "ground_truth_verified": False},
        "source_ref": {
            "origin": origin,
            "origin_source": str(entry["source"] or ""),
            "trace_id": str(entry.get("trace_id") or ""),
        },
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
        fact_evidence_bindings = None
        quality_checks = None
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
                    _current_verify_evidence_anchors,
                    clean_claims,
                    actor.actor,
                    resolve_principal(request),
                )
                quality_checks = validate_ragas_candidate_review(
                    payload=context["payload"],
                    expected=expected,
                    evidence=evidence,
                    fact_evidence_bindings=[
                        item.model_dump(mode="json")
                        for item in body.fact_evidence_bindings
                    ],
                    quality_checks=body.quality_checks,
                    rationale=clean_rationale,
                )
                if quality_checks is not None:
                    fact_evidence_bindings = resolve_fact_evidence_bindings(
                        required_facts=(expected or {}).get("required_facts", []),
                        evidence=evidence,
                        bindings=[
                            item.model_dump(mode="json")
                            for item in body.fact_evidence_bindings
                        ],
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
        elif body.verdict == "rejected" and domain == "rag":
            # 拒绝时保留审核者引用的原始证据；此分支不声称证据已通过锚点核验。
            evidence = clean_claims
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
            fact_evidence_bindings=fact_evidence_bindings,
            quality_checks=quality_checks,
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
                _current_verify_evidence_anchors,
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
                    _current_verify_evidence_anchors,
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
                    getattr(facade, "verify_evidence_anchors", verify_evidence_anchors)(
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

