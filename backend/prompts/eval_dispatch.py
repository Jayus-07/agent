"""Prompt 发布评测分发：本地执行或 GitHub Actions 轮询。"""
from __future__ import annotations

import io
import json
import os
import zipfile
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus


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


@dataclass(frozen=True)
class PollResult:
    """一次 GitHub Actions 轮询的结果。"""

    state: str
    run_id: str = ""
    reason: str = ""


class GitHubActionsClient:
    """GitHub Actions API 客户端，只负责外部 API 和 Artifact 读取。"""

    def __init__(self, config: dict[str, str] | None = None) -> None:
        self._config = config or _github_config()

    async def find_run(self, release: PromptReleaseRecord) -> dict[str, Any] | None:
        """按 release 的 external_run_id 找到 workflow_dispatch 对应的 Run。"""
        data = await self._request_json(
            "GET",
            f"/actions/workflows/{self._config['workflow']}/runs",
            params={
                "branch": self._config["ref"],
                "per_page": "50",
            },
        )
        runs = data.get("workflow_runs") or []
        token = release.external_run_id
        for run in sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True):
            searchable = " ".join(
                str(run.get(field) or "")
                for field in ("display_title", "run_name", "name")
            )
            if token and token in searchable:
                return run
        return None

    async def read_result(
        self, release: PromptReleaseRecord, run: dict[str, Any]
    ) -> dict[str, Any] | None:
        """读取 GitHub Artifact 中的 prompt_eval_result.json。"""
        artifacts = await self._request_json(
            "GET", f"/actions/runs/{int(run['id'])}/artifacts"
        )
        expected_name = f"prompt-eval-{release.release_id}"
        artifact = next(
            (
                item
                for item in artifacts.get("artifacts", [])
                if item.get("name") == expected_name and not item.get("expired")
            ),
            None,
        )
        if not artifact:
            return None

        archive_url = str(artifact.get("archive_download_url") or "")
        if not archive_url:
            return None
        payload = await self._request_bytes("GET", archive_url, absolute=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(
                (
                    name
                    for name in archive.namelist()
                    if name == "prompt_eval_result.json"
                    or name.endswith("/prompt_eval_result.json")
                ),
                None,
            )
            if not member:
                return None
            result = json.loads(archive.read(member).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("prompt_eval_result.json 必须是 JSON object")
        return result

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(method, path, params=params)
        if not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub API 响应必须是 JSON object")
        return payload

    async def _request_bytes(
        self, method: str, path: str, *, absolute: bool = False
    ) -> bytes:
        response = await self._request(method, path, absolute=absolute)
        return response.content

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        absolute: bool = False,
    ):
        import httpx

        url = path if absolute else f"https://api.github.com/repos/{self._config['repository']}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._config['token']}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.request(method, url, headers=headers, params=params)
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"GitHub API {method} {path} 返回 HTTP {response.status_code}"
            )
        return response


class GitHubPromptEvalPoller:
    """轮询 GitHub Run，并将 Artifact 结果幂等写回 Release。"""

    def __init__(self, *, release_service: Any, client: Any | None = None) -> None:
        self._release_service = release_service
        self._client = client or GitHubActionsClient()

    async def poll(self, release: PromptReleaseRecord) -> PollResult:
        if release.status != PromptReleaseStatus.RUNNING:
            return PollResult(state="ignored", reason="release 不在 running 状态")

        run = await self._client.find_run(release)
        if not run:
            return PollResult(state="pending", reason="GitHub Run 尚未可见")

        if str(run.get("status") or "") != "completed":
            return PollResult(state="pending", run_id=str(run.get("id") or ""))

        conclusion = str(run.get("conclusion") or "")
        result = await self._client.read_result(release, run)
        if result is None and conclusion == "success":
            return PollResult(
                state="pending",
                run_id=str(run.get("id") or ""),
                reason="GitHub Run 已完成但 Artifact 尚未可读",
            )

        if result is None:
            result = {
                "status": "failed",
                "run_id": f"github-run-{run.get('id')}",
                "failure_reason": f"GitHub Actions 结论为 {conclusion or 'unknown'}，未生成有效报告",
            }
        self._validate_result_identity(release, result)
        if conclusion != "success" and result.get("status") == "passed":
            result = {
                **result,
                "status": "failed",
                "failure_reason": (
                    f"GitHub Actions 结论为 {conclusion or 'unknown'}，"
                    "不能接受 passed Artifact"
                ),
            }
        provenance = dict(result.get("provenance") or {})
        provenance.update(
            {
                "github_run_id": str(run.get("id") or ""),
                "github_run_url": str(run.get("html_url") or ""),
                "github_conclusion": conclusion,
            }
        )
        result["provenance"] = provenance
        status = str(result.get("status") or "failed")
        await self._release_service.record_result(
            release.release_id,
            result,
            actor="prompt-eval:github-poller",
        )
        return PollResult(
            state=status,
            run_id=str(run.get("id") or ""),
            reason=str(result.get("failure_reason") or ""),
        )

    @staticmethod
    def _validate_result_identity(
        release: PromptReleaseRecord, result: dict[str, Any]
    ) -> None:
        if str(result.get("release_id") or release.release_id) != release.release_id:
            raise ValueError("GitHub Artifact release_id 与当前 Release 不匹配")
        external_run_id = str(result.get("external_run_id") or "")
        if external_run_id and external_run_id != release.external_run_id:
            raise ValueError("GitHub Artifact external_run_id 与当前 Release 不匹配")


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
            "version": str(release.version),
            "suite": release.eval_suite,
            "dataset_version": json.dumps(
                release.dataset_provenance, ensure_ascii=False, separators=(",", ":")
            ),
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
    "GitHubActionsClient",
    "GitHubPromptEvalDispatcher",
    "GitHubPromptEvalPoller",
    "LocalPromptEvalDispatcher",
    "PollResult",
    "PromptEvalDispatcher",
]
