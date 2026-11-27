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


from .schemas import *

router = APIRouter(prefix="/evaluation/v2", tags=["评测平台 V2"])
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "super_admin": 3}

# Pre-defined load hook for tests monkeypatching backend.app.api.routes.evaluation_v2.load
load: Optional[Callable[..., Any]] = None


# Facade dynamic proxy support
facade: Any = None

class FacadeProxy:
    """Transparent facade proxy to ensure 100% backward compatibility and test monkeypatch penetration."""

    def __init__(self, target_modules: list[Any], module_name: str):
        self._target_modules = target_modules
        self._module_name = module_name

    def __getattr__(self, name: str) -> Any:
        for m in self._target_modules:
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(f"module '{self._module_name}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_target_modules", "_module_name"):
            super().__setattr__(name, value)
            return
        updated = False
        for m in self._target_modules:
            if hasattr(m, name):
                setattr(m, name, value)
                updated = True
        if not updated:
            for m in self._target_modules:
                setattr(m, name, value)

    def __dir__(self) -> list[str]:
        keys = set()
        for m in self._target_modules:
            keys.update(dir(m))
        return sorted(keys)

    def __repr__(self) -> str:
        return f"<FacadeProxy for {self._module_name}>"


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
    """V2 评测异常 → 用户可读的 HTTP 错误（按根因分类，不再一律 503 兜底）。

    2026-11-22：此前未知异常一律 503「依赖服务暂时不可用」，生产实测中
    迁移未跑（UndefinedTable）与角色未配置（UndefinedObject，如
    evaluation_v2_app）也被报成依赖服务故障，误导排障方向
    （2026-11-21 生产评测中心全链路 503 实证根因即角色缺失）。
    """
    from psycopg2.errors import (
        InsufficientPrivilege,
        OperationalError as PgOperationalError,
        UndefinedObject,
        UndefinedTable,
        UniqueViolation,
    )

    from backend.evaluation.corpus_snapshots import EmptyCorpusError

    if isinstance(exc, UnsupportedRunnerCapabilityError):
        error = HTTPException(422, "当前 V2 Runner 不支持该方案配置")
        error.user_message = str(exc)
        raise error from exc
    if isinstance(exc, EmptyCorpusError):
        error = HTTPException(422, "知识库没有已发布文档")
        error.user_message = "该知识库没有已发布文档，请先完成文档发布和索引后再运行评测。"
        raise error from exc
    if isinstance(exc, (UndefinedTable, UndefinedObject, InsufficientPrivilege)):
        # 数据库结构/权限初始化缺失：不是"服务暂不可用"，是部署初始化未完成。
        # 保持 503（服务端尚未就绪），但文案指向具体补救动作。
        hint = (
            "评测 V2 数据表不存在，请先执行数据库迁移（scripts/init_db.py）"
            if isinstance(exc, UndefinedTable)
            else "评测 V2 数据库角色/权限未配置，请执行评测 V2 角色初始化（GRANT）"
        )
        error = HTTPException(503, hint)
        error.user_message = f"评测 V2 数据库初始化不完整：{hint}"
        raise error from exc
    if isinstance(exc, PgOperationalError):
        error = HTTPException(503, "评测数据库连接失败")
        error.user_message = "评测数据库连接失败，请检查数据库服务状态后重试。"
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
            "quote": quote,
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


def _load_unanswered_entry(entry_id: int) -> dict[str, Any] | None:
    """读持久化坏例行（ai.unanswered_questions，应用连接——与 admin_unanswered
    同一查询通道；evaluation_v2 租户角色无 ai schema 权限，故不经 _transaction）。"""
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, source, question, trace_id "
            "FROM ai.unanswered_questions WHERE id = %s",
            (entry_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "source": row[1], "question": row[2], "trace_id": row[3]}


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

