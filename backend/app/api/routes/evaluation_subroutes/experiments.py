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

