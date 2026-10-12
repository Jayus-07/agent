"""Prompt release gate 状态机测试。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from backend.prompts.release_models import (
    PromptReleaseStatus,
    PublishGateError,
    ReleaseStateError,
)
from backend.prompts.release_service import PromptReleaseService


@dataclass
class _FakePrompt:
    active_version: int = 1


class _FakeReleaseRepository:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    async def create_release(self, **values: Any) -> dict[str, Any]:
        release_id = str(values["release_id"])
        self._next_id += 1
        row = {
            "release_id": release_id,
            "status": PromptReleaseStatus.PENDING.value,
            "target_env": "production",
            "version": values["version"],
            "prompt_key": values["prompt_key"],
            **values,
        }
        self.records[release_id] = row
        return row

    async def get_release(self, release_id: str) -> dict[str, Any] | None:
        return self.records.get(release_id)

    async def mark_running(self, release_id: str, external_run_id: str) -> dict[str, Any]:
        row = self.records[release_id]
        if row["status"] == PromptReleaseStatus.RUNNING.value:
            assert row["external_run_id"] == external_run_id
            return row
        row["status"] = PromptReleaseStatus.RUNNING.value
        row["external_run_id"] = external_run_id
        return row

    async def record_result(
        self,
        release_id: str,
        *,
        status: str,
        eval_run_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        failure_reason: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        row = self.records[release_id]
        if row["status"] in {
            PromptReleaseStatus.PASSED.value,
            PromptReleaseStatus.FAILED.value,
        }:
            raise ValueError("release already has a terminal evaluation result")
        row.update(
            status=status,
            eval_run_id=eval_run_id,
            metrics=metrics or {},
            provenance=provenance or {},
            failure_reason=failure_reason,
            result_actor=actor,
        )
        return row

    async def approve(
        self, release_id: str, actor: str,
        *, provenance_merge: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.records[release_id]
        if row["status"] != PromptReleaseStatus.PASSED.value:
            raise ValueError("release must be passed before approval")
        if provenance_merge:
            merged = dict(row.get("dataset_provenance") or {})
            merged.update(provenance_merge)
            row["dataset_provenance"] = merged
        row.update(status=PromptReleaseStatus.APPROVED.value, approved_by=actor)
        return row

    async def mark_published(self, release_id: str, actor: str) -> dict[str, Any]:
        row = self.records[release_id]
        if row["status"] != PromptReleaseStatus.APPROVED.value:
            raise ValueError("release must be approved before publish")
        row.update(status=PromptReleaseStatus.PUBLISHED.value, published_by=actor)
        return row

    async def mark_published_in_session(
        self, _session, release_id: str, actor: str,
    ) -> dict[str, Any] | None:
        row = self.records[release_id]
        if row["status"] != PromptReleaseStatus.APPROVED.value:
            return None
        row.update(status=PromptReleaseStatus.PUBLISHED.value, published_by=actor)
        return row

    async def mark_rolled_back(self, release_id: str, actor: str) -> dict[str, Any]:
        row = self.records[release_id]
        row.update(status=PromptReleaseStatus.ROLLED_BACK.value, rollback_by=actor)
        return row


class _FakePromptService:
    def __init__(self) -> None:
        self.prompts = {"test.prompt": _FakePrompt()}
        self.publish_calls: list[tuple[str, int, str, bool]] = []
        self._publish_lock = asyncio.Lock()
        # GATE-13/14：版本 → 模板哈希（测试通过改写这里的值模拟「审批后篡改」）
        self.template_hashes: dict[str, dict[int, str]] = {
            "test.prompt": {1: "hash-v1", 2: "hash-v2"},
        }

    async def get_template_hash(self, key: str, version: int) -> str:
        return self.template_hashes[key][version]

    async def publish(
        self,
        key: str,
        version: int,
        *,
        actor: str,
        role: str = "",
        skip_workflow: bool = False,
        _before_commit=None,
    ) -> dict[str, Any]:
        async with self._publish_lock:
            self.publish_calls.append((key, version, actor, skip_workflow))
            if _before_commit is not None:
                await _before_commit(object())
            self.prompts[key].active_version = version
            return {"active_version": version, "previous_version": 1}


@pytest.fixture
def repo() -> _FakeReleaseRepository:
    return _FakeReleaseRepository()


@pytest.fixture
def prompt_service() -> _FakePromptService:
    return _FakePromptService()


@pytest.fixture
def release_service(repo: _FakeReleaseRepository, prompt_service: _FakePromptService) -> PromptReleaseService:
    return PromptReleaseService(repository=repo, prompt_service=prompt_service)


async def _create_release(service: PromptReleaseService) -> Any:
    return await service.create_release(
        key="test.prompt",
        version=2,
        suite="pr_baseline",
        dataset_version={"version": "5.0.0"},
        actor="admin",
        executor="local",
    )


@pytest.mark.asyncio
async def test_failed_release_does_not_change_active_version(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(release.release_id, {"status": "failed"}, "eval")

    with pytest.raises(PublishGateError):
        await release_service.publish(release.release_id, "admin")

    assert prompt_service.prompts["test.prompt"].active_version == 1
    assert prompt_service.publish_calls == []
    assert repo.records[release.release_id]["status"] == PromptReleaseStatus.FAILED.value


@pytest.mark.asyncio
async def test_passing_release_can_be_approved_and_published(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(
        release.release_id,
        {"status": "passed", "run_id": "eval-1", "metrics": {"mrr": 0.9}},
        "eval",
    )
    await release_service.approve(release.release_id, "admin")
    published = await release_service.publish(release.release_id, "admin")

    assert published.status == PromptReleaseStatus.PUBLISHED
    assert prompt_service.prompts["test.prompt"].active_version == 2
    assert prompt_service.publish_calls == [("test.prompt", 2, "admin", True)]


@pytest.mark.asyncio
async def test_release_state_failure_keeps_active_version_unchanged(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(
        release.release_id, {"status": "passed"}, "eval",
    )
    await release_service.approve(release.release_id, "admin")

    async def fail_publish_in_transaction(_session, _release_id, _actor):
        return None

    repo.mark_published_in_session = fail_publish_in_transaction

    with pytest.raises(ReleaseStateError, match="状态已变化"):
        await release_service.publish(release.release_id, "admin")

    assert prompt_service.prompts["test.prompt"].active_version == 1
    assert repo.records[release.release_id]["status"] == PromptReleaseStatus.APPROVED.value


@pytest.mark.asyncio
async def test_release_publish_error_does_not_expose_exception_details(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(
        release.release_id, {"status": "passed"}, "eval",
    )
    await release_service.approve(release.release_id, "admin")

    async def fail_publish(*_args, **_kwargs):
        raise RuntimeError("synthetic-password=never-a-real-secret")

    prompt_service.publish = fail_publish

    with pytest.raises(ReleaseStateError) as error:
        await release_service.publish(release.release_id, "admin")

    assert "synthetic-password" not in str(error.value)
    assert prompt_service.prompts["test.prompt"].active_version == 1
    assert repo.records[release.release_id]["status"] == PromptReleaseStatus.APPROVED.value


@pytest.mark.asyncio
async def test_concurrent_release_publish_commits_only_one_transition(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(
        release.release_id, {"status": "passed"}, "eval",
    )
    await release_service.approve(release.release_id, "admin")

    results = await asyncio.gather(
        release_service.publish(release.release_id, "admin"),
        release_service.publish(release.release_id, "admin"),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(
        isinstance(result, (PublishGateError, ReleaseStateError))
        for result in results
    ) == 1
    assert prompt_service.prompts["test.prompt"].active_version == 2
    assert repo.records[release.release_id]["status"] == PromptReleaseStatus.PUBLISHED.value
    assert len(prompt_service.publish_calls) == 1


@pytest.mark.asyncio
async def test_mark_running_is_idempotent_for_same_external_run(
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)

    first = await release_service.mark_running(release.release_id, "github-123")
    second = await release_service.mark_running(release.release_id, "github-123")

    assert first.release_id == second.release_id
    assert second.status == PromptReleaseStatus.RUNNING


@pytest.mark.asyncio
async def test_duplicate_terminal_result_is_rejected(
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(release.release_id, {"status": "passed"}, "eval")

    with pytest.raises(ReleaseStateError, match="相反的终态"):
        await release_service.record_result(
            release.release_id,
            {"status": "failed"},
            "callback",
        )


@pytest.mark.asyncio
async def test_rollback_preserves_release_history(
    repo: _FakeReleaseRepository,
    release_service: PromptReleaseService,
) -> None:
    release = await _create_release(release_service)
    await release_service.record_result(release.release_id, {"status": "passed"}, "eval")
    await release_service.approve(release.release_id, "admin")
    await release_service.publish(release.release_id, "admin")
    rolled_back = await release_service.rollback(release.release_id, "admin")

    assert rolled_back.status == PromptReleaseStatus.ROLLED_BACK
    assert repo.records[release.release_id]["version"] == 2


@pytest.mark.asyncio
async def test_create_release_records_candidate_template_hash(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    """GATE-13 前置：创建 release 时必须记录候选模板指纹。"""
    release = await _create_release(release_service)

    stored = repo.records[release.release_id]["dataset_provenance"]
    assert stored["candidate_template_hash"] == {"test.prompt": "hash-v2"}
    assert stored["suite"] == "pr_baseline"


@pytest.mark.asyncio
async def test_approve_rejects_tampered_candidate(
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    """GATE-13：评测通过后模板被改 → 审批拒绝。"""
    release = await _create_release(release_service)
    await release_service.record_result(release.release_id, {"status": "passed"}, "eval")
    prompt_service.template_hashes["test.prompt"][2] = "hash-tampered"

    with pytest.raises(PublishGateError, match="candidate_template_hash"):
        await release_service.approve(release.release_id, "admin")


@pytest.mark.asyncio
async def test_publish_rejects_tamper_after_approval(
    repo: _FakeReleaseRepository,
    prompt_service: _FakePromptService,
    release_service: PromptReleaseService,
) -> None:
    """GATE-14：审批后模板再被改 → 发布拒绝，approval 失效。"""
    release = await _create_release(release_service)
    await release_service.record_result(release.release_id, {"status": "passed"}, "eval")
    await release_service.approve(release.release_id, "admin")
    assert repo.records[release.release_id]["dataset_provenance"][
        "approved_template_hash"
    ] == {"test.prompt": "hash-v2"}

    prompt_service.template_hashes["test.prompt"][2] = "hash-tampered"
    with pytest.raises(PublishGateError, match="审批"):
        await release_service.publish(release.release_id, "admin")
    assert prompt_service.publish_calls == []
