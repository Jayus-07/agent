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


class CaseGoldenMark(StrictModel):
    """Golden 标注请求（批次 6a）：标注独立成表，不触 revision 内容锁。"""
    golden: bool
    reason: str = Field(default="", max_length=1000)


class EvidenceAnchorClaim(StrictModel):
    kb_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    doc_id: str = Field(min_length=1, max_length=200)
    doc_version: str = Field(min_length=1, max_length=200)
    anchor_id: str | None = Field(default=None, min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=8000)


class FactEvidenceBinding(StrictModel):
    """按标准事实序号关联请求证据数组中的引用。"""

    fact_index: StrictInt = Field(ge=0, le=39)
    evidence_indices: list[StrictInt] = Field(min_length=1, max_length=30)


class CandidateReview(StrictModel):
    expected_revision: int = Field(ge=1)
    verdict: Literal["approved", "rejected", "needs_changes"]
    rationale: str = Field(min_length=1, max_length=2000)
    expected: dict[str, Any] | None = None
    evidence: list[EvidenceAnchorClaim] = Field(default_factory=list, max_length=30)
    fact_evidence_bindings: list[FactEvidenceBinding] = Field(
        default_factory=list,
        max_length=40,
        description="将每条必需事实关联到 evidence 数组中的一条或多条引用",
    )
    quality_checks: dict[RagasQualityCheck, StrictBool] | None = None


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


class UnansweredIntake(StrictModel):
    entry_id: int = Field(ge=1)
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

