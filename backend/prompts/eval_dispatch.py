"""Prompt 发布评测分发：本地评测回调或 GitHub Actions 执行。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from backend.prompts.release_models import PromptReleaseRecord


@dataclass(frozen=True)
class DispatchResult:
    """一次评测分发的外部可见结果。"""

    release_id: str
    executor: str
    accepted: bool
    external_run_id: str = ""
    status: str = ""
    error_code: str = ""
    message: str = ""


class LocalPromptEvalDispatcher:
    """在当前服务进程中使用数据库绑定的外部模型执行评测。"""

    def __init__(
        self,
        *,
        release_service: Any | None = None,
        evaluator: Callable[[PromptReleaseRecord], Awaitable[dict[str, Any]]] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._release_service = release_service
        self._evaluator = evaluator
        self._run_id_factory = run_id_factory or (lambda: f"local-{uuid4().hex}")

    async def dispatch(self, release: PromptReleaseRecord) -> DispatchResult:
        service = self._release_service or _load_release_service()
        external_run_id = self._run_id_factory()
        if self._evaluator is None:
            return DispatchResult(
                release_id=release.release_id,
                executor="local",
                accepted=False,
                external_run_id=external_run_id,
                error_code="LOCAL_EVALUATOR_NOT_CONFIGURED",
                message="本地评测执行器未注入评测回调",
            )
        try:
            await service.mark_running(release.release_id, external_run_id)
            result = dict(await self._evaluator(release))
            result.setdefault("status", "failed")
            result.setdefault("external_run_id", external_run_id)
            await service.record_result(
                release.release_id,
                result,
                actor=release.created_by or "prompt-publish:local",
            )
            return DispatchResult(
                release_id=release.release_id,
                executor="local",
                accepted=True,
                external_run_id=external_run_id,
                status=str(result["status"]),
            )
        except Exception as exc:  # noqa: BLE001 — 分发失败要结构化返回
            return DispatchResult(
                release_id=release.release_id,
                executor="local",
                accepted=False,
                external_run_id=external_run_id,
                error_code="DISPATCH_FAILED",
                message=str(exc),
            )


class GitHubPromptEvalDispatcher:
    """通过 GitHub Actions workflow_dispatch 触发外部评测。"""

    def __init__(
        self,
        *,
        release_service: Any | None = None,
        requester: Callable[[str, dict[str, str], dict[str, Any]], Awaitable[int]] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._release_service = release_service
        self._requester = requester or _github_workflow_dispatch
        self._run_id_factory = run_id_factory or (lambda: f"github-{uuid4().hex}")

    async def dispatch(self, release: PromptReleaseRecord) -> DispatchResult:
        config = _github_config()
        external_run_id = self._run_id_factory()
        if not all(config.values()):
            return DispatchResult(
                release_id=release.release_id,
                executor="github",
                accepted=False,
                external_run_id=external_run_id,
                error_code="DISPATCH_NOT_CONFIGURED",
                message=(
                    "GitHub 评测分发未配置 repository/workflow/token，"
                    "凭据只允许放在后端或 GitHub Environment"
                ),
            )

        payload = {
            "release_id": release.release_id,
            "external_run_id": external_run_id,
            "prompt_key": release.prompt_key,
            "version": release.version,
            "suite": release.eval_suite,
            "dataset_version": release.dataset_provenance,
        }
        url = (
            f"https://api.github.com/repos/{config['repository']}"
            f"/actions/workflows/{config['workflow']}/dispatches"
        )
        try:
            status_code = await self._requester(
                url,
                {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {config['token']}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                {"ref": config["ref"], "inputs": payload},
            )
            if status_code < 200 or status_code >= 300:
                return DispatchResult(
                    release_id=release.release_id,
                    executor="github",
                    accepted=False,
                    external_run_id=external_run_id,
                    error_code="GITHUB_DISPATCH_FAILED",
                    message=f"GitHub workflow_dispatch 返回 HTTP {status_code}",
                )
            service = self._release_service
            if service is not None:
                await service.mark_running(release.release_id, external_run_id)
            return DispatchResult(
                release_id=release.release_id,
                executor="github",
                accepted=True,
                external_run_id=external_run_id,
                status="running",
            )
        except Exception as exc:  # noqa: BLE001 — 外部 API 失败要结构化返回
            return DispatchResult(
                release_id=release.release_id,
                executor="github",
                accepted=False,
                external_run_id=external_run_id,
                error_code="GITHUB_DISPATCH_FAILED",
                message=str(exc),
            )


class PromptEvalDispatcher:
    """按 release.executor 选择执行通道。"""

    def __init__(self, *, local: Any | None = None, github: Any | None = None) -> None:
        self._local = local or LocalPromptEvalDispatcher()
        self._github = github or GitHubPromptEvalDispatcher()

    async def dispatch(self, release: PromptReleaseRecord) -> DispatchResult:
        if release.executor == "local":
            return await self._local.dispatch(release)
        if release.executor == "github":
            return await self._github.dispatch(release)
        return DispatchResult(
            release_id=release.release_id,
            executor=release.executor,
            accepted=False,
            error_code="DISPATCH_NOT_SUPPORTED",
            message=f"不支持的评测执行器: {release.executor}",
        )


async def _github_workflow_dispatch(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        return response.status_code


def _github_config() -> dict[str, str]:
    return {
        "repository": os.getenv("PROMPT_EVAL_GITHUB_REPOSITORY", "").strip(),
        "workflow": os.getenv("PROMPT_EVAL_GITHUB_WORKFLOW", "prompt-eval.yml").strip(),
        "token": os.getenv("PROMPT_EVAL_GITHUB_TOKEN", "").strip(),
        "ref": os.getenv("PROMPT_EVAL_GITHUB_REF", "main").strip(),
    }


def _load_release_service() -> Any:
    from backend.prompts.release_service import PromptReleaseService

    return PromptReleaseService()


__all__ = [
    "DispatchResult",
    "GitHubPromptEvalDispatcher",
    "LocalPromptEvalDispatcher",
    "PromptEvalDispatcher",
]
