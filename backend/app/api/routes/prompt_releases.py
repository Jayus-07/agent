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
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["get_release_service", "publish_approved_release", "router"]
