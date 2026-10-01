"""GitHub Prompt 评测轮询任务测试。"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.prompts.eval_dispatch import PollResult
from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus
from backend.tasks.prompt_eval_tasks import poll_github_prompt_eval_once


@dataclass
class _FakeReleaseService:
    release: PromptReleaseRecord

    async def get(self, release_id: str) -> PromptReleaseRecord:
        assert release_id == self.release.release_id
        return self.release


class _FakePoller:
    def __init__(self, result: PollResult) -> None:
        self.result = result
        self.releases: list[str] = []

    async def poll(self, release: PromptReleaseRecord) -> PollResult:
        self.releases.append(release.release_id)
        return self.result


def _release() -> PromptReleaseRecord:
    return PromptReleaseRecord(
        release_id="rel-1",
        prompt_key="rag.qa",
        version=2,
        status=PromptReleaseStatus.RUNNING,
        eval_suite="pr_smoke",
        external_run_id="github-run-1",
        executor="github",
    )


@pytest.mark.asyncio
async def test_poll_once_loads_release_by_id_and_delegates_to_poller():
    service = _FakeReleaseService(_release())
    poller = _FakePoller(PollResult(state="pending", reason="run 尚未完成"))

    result = await poll_github_prompt_eval_once(
        "rel-1", release_service=service, poller=poller
    )

    assert result.state == "pending"
    assert poller.releases == ["rel-1"]
