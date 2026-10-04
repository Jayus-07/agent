"""Prompt 候选发布与评测门禁 API。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, resolve_operator_role
from backend.config.settings import PROMPT_EVAL_EXECUTOR
from backend.prompts.registry import PROMPT_REGISTRY
from backend.prompts.eval_dispatch import (
    GitHubPromptEvalDispatcher,
    LocalPromptEvalDispatcher,
    PromptEvalDispatcher,
)
from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseError
from backend.prompts.release_service import PromptReleaseService

router = APIRouter(prefix="/prompts", tags=["Prompt发布门禁"])
logger = logging.getLogger(__name__)
_dispatch_tasks: set[asyncio.Task[None]] = set()


class CreateReleaseRequest(BaseModel):
    suite: str = Field(min_length=1)
    dataset_version: dict[str, Any] = Field(default_factory=dict)
    executor: Literal["local", "github"] = Field(
        default="github" if PROMPT_EVAL_EXECUTOR not in {"local", "github"}
        else PROMPT_EVAL_EXECUTOR
    )
    target_env: str = "production"


def get_release_service() -> PromptReleaseService:
    """服务工厂，便于 API 测试替换，生产使用 DB 仓储。"""
    return PromptReleaseService()


def _check_permission(key: str, action: str, role: str) -> None:
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {key}")
    from backend.app.api.routes.prompts import _check_permission as check_prompt_permission

    check_prompt_permission(spec.risk_level, action, role)


def _to_dict(record: PromptReleaseRecord | Any) -> dict[str, Any]:
    if not isinstance(record, PromptReleaseRecord):
        record = PromptReleaseRecord.from_row(vars(record))
    return {
        "release_id": record.release_id,
        "prompt_key": record.prompt_key,
        "version": record.version,
        "target_env": record.target_env,
        "status": record.status.value,
        "eval_suite": record.eval_suite,
        "executor": record.executor,
        "dataset_provenance": record.dataset_provenance,
        "prompt_snapshot": record.prompt_snapshot,
        "tool_contract_fingerprint": record.tool_contract_fingerprint,
        "model_binding_fingerprint": record.model_binding_fingerprint,
        "eval_run_id": record.eval_run_id,
        "external_run_id": record.external_run_id,
        "metrics": record.metrics,
        "failure_reason": record.failure_reason,
        "created_by": record.created_by,
        "approved_by": record.approved_by,
        "published_by": record.published_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "published_at": record.published_at,
    }


def _with_runtime_status(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from backend.prompts.hot_reload import runtime_status

        payload["runtime_status"] = runtime_status()
    except Exception as exc:  # noqa: BLE001 — 状态查询不能影响发布记录读取
        payload["runtime_status"] = {"status": "unknown", "error": str(exc)}
    return payload


def _gate_blocked(key: str, version: int) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "code": "PROMPT_RELEASE_GATE_BLOCKED",
            "message": "Prompt 尚无通过并审批的发布评测记录",
            "prompt_key": key,
            "version": version,
        },
    )


async def publish_approved_release(
    key: str,
    version: int,
    actor: str,
    role: str,
) -> PromptReleaseRecord:
    _check_permission(key, "publish", role)
    service = get_release_service()
    release = await service.get_approved_release(key, version)
    if release is None:
        raise PromptReleaseError("Prompt release gate blocked")
    return await service.publish(release.release_id, actor)


def _schedule_eval_dispatch(record: PromptReleaseRecord, service: PromptReleaseService) -> None:
    """提交后异步分发评测，避免管理端请求等待模型完成。"""
    task = asyncio.create_task(_dispatch_release(record, service))
    _dispatch_tasks.add(task)
    task.add_done_callback(_dispatch_tasks.discard)


def _enqueue_github_poll(release_id: str):
    """投递持久轮询任务；业务 payload 只携带 release_id。"""
    from backend.tasks.prompt_eval_tasks import enqueue_github_prompt_eval_poll

    return enqueue_github_prompt_eval_poll(release_id)


async def _dispatch_release(record: PromptReleaseRecord, service: PromptReleaseService) -> None:
    dispatcher = PromptEvalDispatcher(
        local=LocalPromptEvalDispatcher(
            release_service=service,
            evaluator=_load_local_evaluator(),
        ),
        github=GitHubPromptEvalDispatcher(release_service=service),
    )
    result = await dispatcher.dispatch(record)
    if result.accepted:
        if record.executor == "github":
            try:
                _enqueue_github_poll(record.release_id)
            except Exception as exc:  # noqa: BLE001 — 入队失败必须终止门禁
                logger.error("Prompt release poll enqueue failed: %s", exc)
                try:
                    await service.record_result(
                        record.release_id,
                        {
                            "status": "failed",
                            "run_id": result.external_run_id,
                            "failure_reason": f"GitHub 评测轮询任务入队失败: {exc}",
                        },
                        actor="prompt-publish:poll-enqueue",
                    )
                except Exception as persist_exc:  # noqa: BLE001
                    logger.error("Prompt release enqueue failure persist failed: %s", persist_exc)
        return
    try:
        await service.record_result(
            record.release_id,
            {
                "status": "failed",
                "run_id": result.external_run_id,
                "failure_reason": result.message or result.error_code,
            },
            actor="prompt-publish:dispatcher",
        )
    except Exception as exc:  # noqa: BLE001 — 评测失败不能反向打断请求进程
        logger.error("Prompt release dispatch failed to persist: %s", exc)


def _load_local_evaluator():
    """在应用层注入评测适配器，避免 prompts 层反向依赖 evaluation。"""
    from backend.evaluation.prompt_release_runner import run_prompt_release_evaluation

    return run_prompt_release_evaluation


@router.post("/{key}/versions/{version}/release", status_code=202)
async def create_release(
    key: str,
    version: int,
    body: CreateReleaseRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    _check_permission(key, "draft", operator.role)
    try:
        service = get_release_service()
        record = await service.create_release(
            key=key,
            version=version,
            suite=body.suite,
            dataset_version=body.dataset_version,
            actor=operator.actor,
            executor=body.executor,
            target_env=body.target_env,
            prompt_snapshot={key: version},
        )
        _schedule_eval_dispatch(record, service)
        return _to_dict(record)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{key}/releases")
async def list_releases(
    key: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    _check_permission(key, "read", operator.role)
    records = await get_release_service().list_releases(key)
    return {"items": [_to_dict(record) for record in records]}


@router.get("/{key}/releases/{release_id}")
async def get_release(
    key: str,
    release_id: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    _check_permission(key, "read", operator.role)
    try:
        record = await get_release_service().get(release_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record.prompt_key != key:
        raise HTTPException(status_code=404, detail="Prompt release 不存在")
    return _with_runtime_status(_to_dict(record))


@router.post("/{key}/releases/{release_id}/approve")
async def approve_release(
    key: str,
    release_id: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    _check_permission(key, "publish", operator.role)
    try:
        record = await get_release_service().approve(release_id, operator.actor)
        if record.prompt_key != key:
            raise KeyError(release_id)
        return _to_dict(record)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PromptReleaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{key}/releases/{release_id}/publish")
async def publish_release(
    key: str,
    release_id: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    _check_permission(key, "publish", operator.role)
    service = get_release_service()
    try:
        record = await service.get(release_id)
        if record.prompt_key != key:
            raise KeyError(release_id)
        published = await service.publish(release_id, operator.actor)
        return _with_runtime_status(_to_dict(published))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PromptReleaseError as exc:
        # GATE-16：门禁拒绝携带结构化 blocked_rules（其余业务拒绝保持原样）
        rules = getattr(exc, "blocked_rules", None)
        if rules:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "PROMPT_RELEASE_GATE_BLOCKED",
                    "message": str(exc),
                    "prompt_key": key,
                    "blocked_rules": rules,
                },
            )
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{key}/releases/{release_id}/comparison")
async def get_release_comparison(
    key: str,
    release_id: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    """REG-08：审批页 candidate vs production 对比数据。

    聚合三路信息：候选 release 的评测指标（含 gate 判定）、production
    当前版本的最近评测 run、基线文件（可能 baseline_unavailable）。
    前端渲染 current / baseline / delta 三列表格；无对比维度时显式
    标注不可用，不伪造 delta=0。
    """
    _check_permission(key, "read", operator.role)
    service = get_release_service()
    try:
        record = await service.get(release_id)
        if record.prompt_key != key:
            raise KeyError(release_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    candidate = _comparison_view(record.metrics or {}, record.eval_run_id)
    gate = (record.metrics or {}).get("gate") or {}
    regression = gate.get("regression") or {}
    dataset_version = str(
        regression.get("baseline_dataset_version")
        or record.dataset_provenance.get("version")
        or record.dataset_provenance.get("dataset_version")
        or ""
    )

    # production 当前版本与其最近评测 run（REG-08 的对照面）
    production_version: int | None = None
    production_runs: list[dict[str, Any]] = []
    try:
        from backend.prompts.service import prompt_service

        aliases = await prompt_service.get_aliases(key)
        production_version = aliases.get("production")
    except Exception:  # noqa: BLE001 — 对比面板容错：别名读取失败不阻塞
        production_version = None
    if production_version is not None:
        production_runs = _prompt_version_runs(key, production_version)

    baseline_available = bool(regression.get("baseline_available"))
    baseline_view: dict[str, Any] = (
        {
            "available": True,
            "run_id": regression.get("baseline_run_id", ""),
            "dataset_version": dataset_version,
        }
        if baseline_available
        else {
            "available": False,
            "note": "baseline unavailable（无基线，回归门已跳过并留痕）",
        }
    )

    return {
        "release_id": release_id,
        "prompt_key": key,
        "candidate_version": record.version,
        "production_version": production_version,
        "candidate": candidate,
        "production_runs": production_runs,
        "baseline": baseline_view,
        "deltas": _candidate_deltas(candidate, regression),
    }


def _comparison_view(metrics: dict[str, Any], eval_run_id: str) -> dict[str, Any]:
    """候选指标视图：自研核心指标 + gate 判定 + RAGAS（独立键，不混用）。"""
    core_keys = (
        "pass_rate", "recall@5", "recall@10", "mrr", "ndcg@10",
        "top1_accuracy", "reject_accuracy", "sem_faithfulness",
        "sem_answer_correctness",
    )
    candidate: dict[str, Any] = {
        "eval_run_id": eval_run_id,
        "metrics": {k: metrics.get(k) for k in core_keys if metrics.get(k) is not None},
        "ragas": {
            k: v for k, v in metrics.items()
            if k.startswith("ragas_") and k != "ragas_reason" and v is not None
        },
    }
    gate = metrics.get("gate")
    if gate:
        candidate["gate"] = {
            "tier_pass": gate.get("tier_pass"),
            "sample_pass": gate.get("sample_pass"),
            "regression_pass": (gate.get("regression") or {}).get("regression_pass"),
            "ragas_pass": (gate.get("ragas") or {}).get("ragas_pass"),
            "blocked_rules": gate.get("blocked_rules", []),
        }
    return candidate


def _prompt_version_runs(key: str, version: int, limit: int = 5) -> list[dict[str, Any]]:
    """production 版本最近评测 run（复用台账 JSONB 反查，软失败）。"""
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT run_id, pass_rate, case_count, pass_count, metrics, created_at
                FROM ai.eval_run_records
                WHERE prompt_snapshot @> %s::jsonb
                ORDER BY created_at DESC LIMIT %s
                """,
                (__import__("json").dumps({key: str(version)}), limit),
            )
            rows = cur.fetchall()
        return [
            {
                "run_id": r[0],
                "pass_rate": r[1],
                "case_count": r[2],
                "pass_count": r[3],
                "metrics": (r[4] or {}).get("rag", {}).get("metrics", {})
                if isinstance(r[4], dict) else {},
                "created_at": str(r[5]),
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — 台账不可达时对比面板降级为空列表
        return []


def _candidate_deltas(
    candidate: dict[str, Any], regression: dict[str, Any],
) -> dict[str, Any]:
    """candidate vs baseline 的指标 delta（来自回归门结构化结果）。"""
    if not regression.get("baseline_available"):
        return {"available": False, "note": regression.get("messages", ["baseline unavailable"])[0]}
    deltas = {
        entry["metric"]: {
            "baseline": entry["baseline"],
            "current": entry["current"],
            "delta": entry["delta"],
        }
        for entry in regression.get("errors", []) + regression.get("warnings", [])
    }
    return {"available": True, "regressions": deltas}


__all__ = ["get_release_service", "publish_approved_release", "router"]
