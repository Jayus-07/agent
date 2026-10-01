"""Prompt 评测分发器契约测试。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus
from backend.prompts.eval_dispatch import (
    DispatchResult,
    GitHubPromptEvalDispatcher,
    LocalPromptEvalDispatcher,
    PromptEvalDispatcher,
)


@dataclass
class _FakeReleaseService:
    release: PromptReleaseRecord
    marked_running: list[tuple[str, str]]
    results: list[tuple[str, dict[str, Any], str]]

    async def mark_running(self, release_id: str, external_run_id: str):
        self.marked_running.append((release_id, external_run_id))
        self.release = PromptReleaseRecord(
            **{**self.release.__dict__, "status": PromptReleaseStatus.RUNNING,
               "external_run_id": external_run_id}
        )
        return self.release

    async def record_result(self, release_id: str, result: dict[str, Any], actor: str):
        self.results.append((release_id, result, actor))
        self.release = PromptReleaseRecord(
            **{**self.release.__dict__,
               "status": PromptReleaseStatus(result["status"]),
               "eval_run_id": str(result.get("run_id") or "")}
        )
        return self.release


def _release(executor: str = "local") -> PromptReleaseRecord:
    return PromptReleaseRecord(
        release_id="rel-1",
        prompt_key="rag.qa",
        version=2,
        status=PromptReleaseStatus.PENDING,
        eval_suite="pr_baseline",
        dataset_provenance={"version": "5.0.0"},
        executor=executor,
    )


@pytest.mark.asyncio
async def test_local_dispatch_uses_selected_suite_and_records_terminal_result():
    service = _FakeReleaseService(_release(), [], [])
    original_release = service.release
    evaluator = AsyncMock(
        return_value={
            "status": "passed",
            "run_id": "eval-local-1",
            "metrics": {"pass_rate": 1.0},
        }
    )
    dispatcher = LocalPromptEvalDispatcher(
        release_service=service,
        evaluator=evaluator,
        run_id_factory=lambda: "local-run-1",
    )

    result = await dispatcher.dispatch(service.release)

    assert result == DispatchResult(
        release_id="rel-1",
        executor="local",
        accepted=True,
        external_run_id="local-run-1",
        status="passed",
    )
    evaluator.assert_awaited_once_with(original_release)
    assert service.marked_running == [("rel-1", "local-run-1")]
    assert service.results[0][1]["run_id"] == "eval-local-1"


@pytest.mark.asyncio
async def test_github_dispatch_without_configuration_returns_structured_failure(
    monkeypatch,
):
    for name in (
        "PROMPT_EVAL_GITHUB_REPOSITORY",
        "PROMPT_EVAL_GITHUB_WORKFLOW",
        "PROMPT_EVAL_GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    result = await GitHubPromptEvalDispatcher().dispatch(_release("github"))

    assert result.accepted is False
    assert result.error_code == "DISPATCH_NOT_CONFIGURED"
    assert result.release_id == "rel-1"


@pytest.mark.asyncio
async def test_dispatcher_selects_executor_without_frontend_credentials():
    local = AsyncMock()
    github = AsyncMock()
    github.dispatch = AsyncMock(return_value=DispatchResult(
        release_id="rel-1", executor="github", accepted=True,
        external_run_id="gh-1", status="running",
    ))
    dispatcher = PromptEvalDispatcher(local=local, github=github)

    result = await dispatcher.dispatch(_release("github"))

    assert result.external_run_id == "gh-1"
    local.assert_not_awaited()
    github.dispatch.assert_awaited_once()
