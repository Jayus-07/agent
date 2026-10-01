"""Prompt 发布评测分发：本地 DB 模型执行或 GitHub Actions 执行。"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
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
        self._evaluator = evaluator or _run_local_evaluation
        self._run_id_factory = run_id_factory or (lambda: f"local-{uuid4().hex}")

    async def dispatch(self, release: PromptReleaseRecord) -> DispatchResult:
        service = self._release_service or _load_release_service()
        external_run_id = self._run_id_factory()
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


async def _run_local_evaluation(release: PromptReleaseRecord) -> dict[str, Any]:
    """用现有 EvaluationService 执行 suite，模型解析仍来自 DB 注册表。"""
    return await asyncio.to_thread(_run_local_evaluation_sync, release)


def _run_local_evaluation_sync(release: PromptReleaseRecord) -> dict[str, Any]:
    import asyncio as _asyncio

    from backend.evaluation.runner import run_all
    from backend.evaluation.storage import persist_report
    from backend.infra.llm.registry_store import refresh_registry

    with _evaluation_env(release.created_by):
        _asyncio.run(refresh_registry())
        report = run_all(
            module="rag",
            live=True,
            selection=release.eval_suite,
            dataset_version=str(
                release.dataset_provenance.get("version")
                or release.dataset_provenance.get("dataset_version")
                or ""
            ),
        )
        run_dir = persist_report(report)

    summaries = [summary for summary in report.summaries if summary.module == "rag"]
    summary = summaries[0] if summaries else None
    tier_ok = all(item.passed_threshold for item in report.tier_summaries)
    passed = bool(summary and tier_ok)
    metrics = dict(summary.metrics if summary else {})
    if summary is not None:
        metrics["pass_rate"] = summary.pass_rate
        metrics["total"] = summary.total
        metrics["passed"] = summary.passed
    return {
        "status": "passed" if passed else "failed",
        "run_id": run_dir.name,
        "metrics": metrics,
        "failure_reason": "评测层级未达到阈值" if not passed else "",
    }


@contextmanager
def _evaluation_env(triggered_by: str):
    old_trigger = os.environ.get("EVAL_TRIGGER")
    old_triggered_by = os.environ.get("EVAL_TRIGGERED_BY")
    os.environ["EVAL_TRIGGER"] = "prompt_publish"
    os.environ["EVAL_TRIGGERED_BY"] = triggered_by or "prompt-publish"
    try:
        yield
    finally:
        _restore_env("EVAL_TRIGGER", old_trigger)
        _restore_env("EVAL_TRIGGERED_BY", old_triggered_by)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _load_release_service() -> Any:
    from backend.prompts.release_service import PromptReleaseService

    return PromptReleaseService()


__all__ = [
    "DispatchResult",
    "GitHubPromptEvalDispatcher",
    "LocalPromptEvalDispatcher",
    "PromptEvalDispatcher",
]
