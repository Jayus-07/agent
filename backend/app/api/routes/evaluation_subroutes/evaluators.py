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
from .base import _role, _safe_error
from .schemas import *

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


@router.get("/domain-overview")
async def get_domain_overview(
    identity: Identity = Depends(require_product_identity),
    actor: OperatorIdentity = Depends(require_user_actor),
):
    """全域（rag, sql, cs, travel, agent, security）质量与最新评测状态统一总览。"""
    _role(actor, "viewer")
    try:
        return await asyncio.to_thread(repository.get_domain_overview, identity.tenant_id)
    except Exception as exc:  # noqa: BLE001
        _safe_error(exc)

