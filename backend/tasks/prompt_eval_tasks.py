"""Prompt 发布评测的 GitHub Actions 轮询任务。"""
from __future__ import annotations

import asyncio
from typing import Any

from backend.config.settings import (
    PROMPT_EVAL_POLL_INTERVAL_SECONDS,
    PROMPT_EVAL_POLL_MAX_ATTEMPTS,
)
from backend.prompts.eval_dispatch import GitHubPromptEvalPoller, PollResult
from backend.prompts.release_service import PromptReleaseService
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app
from backend.tasks.queue_router import resolve_for_celery_task

TASK_NAME = "prompts.poll_github_eval"


async def poll_github_prompt_eval_once(
    release_id: str,
    *,
    release_service: Any | None = None,
    poller: Any | None = None,
) -> PollResult:
    """执行一次轮询；Celery 只负责重试和调度，不承载业务状态。"""
    service = release_service or PromptReleaseService()
    release = await service.get(release_id)
    evaluator = poller or GitHubPromptEvalPoller(release_service=service)
    return await evaluator.poll(release)


async def _mark_poll_timeout(release_id: str, reason: str) -> None:
    service = PromptReleaseService()
    release = await service.get(release_id)
    if release.status.value != "running":
        return
    await service.record_result(
        release_id,
        {
            "status": "failed",
            "run_id": release.external_run_id,
            "failure_reason": reason,
        },
        actor="prompt-eval:github-poller",
    )


@celery_app.task(
    bind=True,
    name=TASK_NAME,
    acks_late=True,
    max_retries=PROMPT_EVAL_POLL_MAX_ATTEMPTS,
)
def poll_github_prompt_eval(self, release_id: str) -> dict[str, Any]:
    """查询一次 GitHub Actions；未完成时将同一个 release_id 延迟重投。"""
    try:
        result = asyncio.run(poll_github_prompt_eval_once(release_id))
    except Exception as exc:  # noqa: BLE001 — 外部 GitHub 暂时不可用时重试
        if self.request.retries >= PROMPT_EVAL_POLL_MAX_ATTEMPTS:
            reason = f"GitHub Actions 轮询超时: {type(exc).__name__}: {exc}"
            asyncio.run(_mark_poll_timeout(release_id, reason))
            return {"state": "failed", "reason": reason}
        logger.warning(
            "[PromptEvalPoller] release=%s GitHub 查询失败，将重试: %s",
            release_id,
            exc,
        )
        raise self.retry(
            exc=exc,
            countdown=PROMPT_EVAL_POLL_INTERVAL_SECONDS,
            max_retries=PROMPT_EVAL_POLL_MAX_ATTEMPTS,
        ) from exc

    if result.state == "pending":
        if self.request.retries >= PROMPT_EVAL_POLL_MAX_ATTEMPTS:
            reason = result.reason or "GitHub Actions 评测超时"
            asyncio.run(_mark_poll_timeout(release_id, reason))
            return {"state": "failed", "reason": reason}
        raise self.retry(
            countdown=PROMPT_EVAL_POLL_INTERVAL_SECONDS,
            max_retries=PROMPT_EVAL_POLL_MAX_ATTEMPTS,
        )

    return {
        "state": result.state,
        "run_id": result.run_id,
        "reason": result.reason,
    }


def enqueue_github_prompt_eval_poll(release_id: str):
    """将轮询任务投递到 maintenance 队列，payload 仅保留 release_id。"""
    route = resolve_for_celery_task(TASK_NAME)
    return poll_github_prompt_eval.apply_async(
        args=[release_id],
        queue=route.physical_queue,
    )


__all__ = [
    "TASK_NAME",
    "enqueue_github_prompt_eval_poll",
    "poll_github_prompt_eval",
    "poll_github_prompt_eval_once",
]
