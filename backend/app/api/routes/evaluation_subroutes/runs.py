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


from .base import *
from .base import (
    _role,
    _build_run_evaluator_snapshot,
    _safe_error,
    _safe_slice_tags,
    _run_fingerprint,
)
from .schemas import *

@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_ledger: bool = Query(True, description="是否聚合包含 CLI 与发布门禁台账记录"),
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        return await asyncio.to_thread(
            repository.list_runs_unified,
            identity.tenant_id,
            limit=limit,
            offset=offset,
            include_ledger=include_ledger,
        )
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
    run_id: str,
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    _role(actor, "admin")
    try:
        run = await asyncio.to_thread(repository.get_run_unified, identity.tenant_id, str(run_id))
        return {"run": run, "fingerprint": _run_fingerprint(run.get("snapshot_json"))}
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)


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
        raise HTTPException(
            404,
            "该运行的逐题报告文件未找到，无法查看完整回答和证据。请重新运行评测生成新报告。",
        ) from exc
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

