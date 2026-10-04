"""Prompt 发布门禁服务。"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.prompts.release_models import (
    PromptReleaseRecord,
    PromptReleaseStatus,
    PublishGateError,
    ReleaseStateError,
)
from backend.prompts.release_repository import PromptReleaseRepository


class PromptReleaseService:
    """编排发布记录状态机，不在此处复制 PromptService 的发布实现。"""

    def __init__(self, *, repository=None, prompt_service=None) -> None:
        self._repository = repository or PromptReleaseRepository()
        self._prompt_service = prompt_service

    async def create_release(
        self,
        key: str,
        version: int,
        suite: str,
        dataset_version: dict[str, Any],
        actor: str,
        executor: str,
        *,
        target_env: str = "production",
        prompt_snapshot: dict[str, Any] | None = None,
        tool_contract_fingerprint: str = "",
        model_binding_fingerprint: str = "",
    ) -> PromptReleaseRecord:
        if not key.strip():
            raise ValueError("Prompt key 不能为空")
        if version < 1:
            raise ValueError("Prompt version 必须为正整数")
        if not suite.strip():
            raise ValueError("评测 suite 不能为空")
        if executor not in {"local", "github"}:
            raise ValueError("executor 只支持 local 或 github")
        provenance = dict(dataset_version)
        provenance.setdefault("suite", suite)
        # GATE-13/14：创建时记录候选模板内容指纹。release_candidate_hash 的
        # 起点——审批/发布两点据此校验「审批的内容 == 评测的内容 == 发布的内容」。
        prompt_service = self._prompt_service or _load_prompt_service()
        provenance["candidate_template_hash"] = {
            key: await prompt_service.get_template_hash(key, version),
        }
        row = await self._repository.create_release(
            release_id=f"rel-{uuid4().hex}",
            prompt_key=key,
            version=version,
            target_env=target_env,
            eval_suite=suite,
            dataset_provenance=provenance,
            prompt_snapshot=prompt_snapshot or {},
            tool_contract_fingerprint=tool_contract_fingerprint,
            model_binding_fingerprint=model_binding_fingerprint,
            executor=executor,
            created_by=actor,
        )
        return PromptReleaseRecord.from_row(row)

    async def get(self, release_id: str) -> PromptReleaseRecord:
        row = await self._repository.get_release(release_id)
        if row is None:
            raise KeyError(f"Prompt release 不存在: {release_id}")
        return PromptReleaseRecord.from_row(row)

    async def list_releases(self, key: str) -> list[PromptReleaseRecord]:
        rows = await self._repository.list_releases(key)
        return [PromptReleaseRecord.from_row(row) for row in rows]

    async def get_approved_release(
        self, key: str, version: int, *, target_env: str = "production"
    ) -> PromptReleaseRecord | None:
        releases = await self.list_releases(key)
        return next(
            (
                release for release in releases
                if release.version == version
                and release.target_env == target_env
                and release.status == PromptReleaseStatus.APPROVED
            ),
            None,
        )

    async def mark_running(self, release_id: str, external_run_id: str) -> PromptReleaseRecord:
        current = await self.get(release_id)
        if current.status == PromptReleaseStatus.RUNNING:
            if current.external_run_id != external_run_id:
                raise ReleaseStateError("同一 release 不允许绑定不同 external run")
            return current
        if current.status != PromptReleaseStatus.PENDING:
            raise ReleaseStateError(
                f"release 状态 {current.status.value} 不允许进入 running"
            )
        row = await self._repository.mark_running(release_id, external_run_id)
        return PromptReleaseRecord.from_row(row)

    async def record_result(
        self,
        release_id: str,
        result: dict[str, Any],
        actor: str,
    ) -> PromptReleaseRecord:
        status = str(result.get("status", ""))
        if status not in {
            PromptReleaseStatus.PASSED.value,
            PromptReleaseStatus.FAILED.value,
        }:
            raise ValueError("评测终态只能是 passed 或 failed")

        current = await self.get(release_id)
        if current.status in {
            PromptReleaseStatus.PASSED,
            PromptReleaseStatus.FAILED,
        }:
            if current.status.value == status:
                return current
            raise ReleaseStateError("release 已有相反的终态评测结果")
        if current.status not in {
            PromptReleaseStatus.PENDING,
            PromptReleaseStatus.RUNNING,
        }:
            raise ReleaseStateError(
                f"release 状态 {current.status.value} 不允许写入评测结果"
            )

        row = await self._repository.record_result(
            release_id,
            status=status,
            eval_run_id=result.get("run_id") or result.get("eval_run_id"),
            metrics=result.get("metrics") or {},
            provenance=result.get("provenance") or {},
            failure_reason=str(result.get("failure_reason") or ""),
            actor=actor,
        )
        return PromptReleaseRecord.from_row(row)

    async def approve(self, release_id: str, actor: str) -> PromptReleaseRecord:
        current = await self.get(release_id)
        if current.status != PromptReleaseStatus.PASSED:
            raise PublishGateError("只有 passed 的评测记录才能审批")
        # GATE-13：审批时重验候选快照——评测通过后模板若被改动，审批必须拒绝，
        # 杜绝「评的是 A、批的是 B」。存量无指纹的 release 跳过比对（向后兼容），
        # 但审批哈希照常落库，供发布点校验。
        provenance = dict(current.dataset_provenance or {})
        candidate_hashes = provenance.get("candidate_template_hash") or {}
        recorded = candidate_hashes.get(current.prompt_key, "")
        current_hash = await (self._prompt_service or _load_prompt_service()) \
            .get_template_hash(current.prompt_key, current.version)
        if recorded and recorded != current_hash:
            raise PublishGateError(
                "候选版本模板在评测通过后发生了变更（candidate_template_hash 不一致），"
                "本审批被拒绝；请重新创建发布评测"
            )
        row = await self._repository.approve(
            release_id,
            actor,
            provenance_merge={"approved_template_hash": {current.prompt_key: current_hash}},
        )
        return PromptReleaseRecord.from_row(row)

    async def publish(self, release_id: str, actor: str) -> PromptReleaseRecord:
        current = await self.get(release_id)
        if current.status != PromptReleaseStatus.APPROVED:
            raise PublishGateError(
                f"Prompt release 尚未审批通过，当前状态为 {current.status.value}"
            )
        # C1-1/C1-7（GATE-03/11）：发布门禁 = tier + 样本量（记录侧已判，
        # failed 不可能走到 approved）之后，再按灰度开关裁决回归门与 RAGAS 门。
        # audit 模式只计算落痕不拦截；enforce 模式 fail-closed 409；
        # baseline_unavailable / RAGAS 未执行在 audit 下降级放行并留痕，
        # enforce 下 RAGAS 未执行视为 blocked（回归门无基线仍放行，REG-10）。
        self._enforce_publish_gates(current)
        prompt_service = self._prompt_service or _load_prompt_service()
        # GATE-14：审批后篡改防线——发布点重验模板指纹必须等于审批时留痕。
        # release_candidate_hash == approved_snapshot_hash == published_snapshot_hash
        # 三点一致才允许发布；不一致 = approval 失效，须重新评测审批。
        approved_hashes = (current.dataset_provenance or {}).get("approved_template_hash") or {}
        recorded = approved_hashes.get(current.prompt_key, "")
        if recorded:
            current_hash = await prompt_service.get_template_hash(
                current.prompt_key, current.version,
            )
            if recorded != current_hash:
                raise PublishGateError(
                    "候选版本模板在审批后被修改（approved_template_hash 不一致），"
                    "原审批已失效；请重新评测并审批后再发布"
                )
        try:
            # Release Gate 的审批已经绑定了通过的外部评测结果；它是生产发布
            # 的最终门禁，不应再要求候选版本先手工走一遍独立的状态流水线。
            # skip_workflow 只跳过 Prompt 版本状态校验，仍保留模板校验、
            # active_version/production alias、审计与热更新通知。
            await prompt_service.publish(
                current.prompt_key,
                current.version,
                actor=actor,
                role="release_gate",
                skip_workflow=True,
            )
        except Exception as exc:
            raise ReleaseStateError(f"Prompt production 发布失败: {exc}") from exc

        row = await self._repository.mark_published(release_id, actor)
        return PromptReleaseRecord.from_row(row)

    async def rollback(self, release_id: str, actor: str) -> PromptReleaseRecord:
        current = await self.get(release_id)
        if current.status != PromptReleaseStatus.PUBLISHED:
            raise ReleaseStateError("只有 published release 才能标记回滚")
        row = await self._repository.mark_rolled_back(release_id, actor)
        return PromptReleaseRecord.from_row(row)

    @staticmethod
    def _enforce_publish_gates(current: PromptReleaseRecord) -> None:
        """C1-1/C1-7：按灰度开关裁决回归门与 RAGAS 门（GATE-11/03）。"""
        from backend.config.settings import (
            PROMPT_RELEASE_RAGAS_GATE_ENABLED,
            PROMPT_RELEASE_REGRESSION_GATE_ENABLED,
        )

        gate = (current.metrics or {}).get("gate") or {}
        blocked: list[dict] = []

        regression = gate.get("regression") or {}
        regression_pass = regression.get("regression_pass")
        if (
            PROMPT_RELEASE_REGRESSION_GATE_ENABLED == "enforce"
            and regression_pass is False
        ):
            details = [
                entry.get("metric", "?") for entry in regression.get("errors", [])
            ]
            blocked.append({
                "rule": "baseline_regression",
                "expected": "候选版本指标不低于基线允许阈值",
                "actual": f"回归指标：{', '.join(details) or '见 metrics.gate.regression'}",
                "severity": "error",
            })

        ragas = gate.get("ragas") or {}
        if (
            PROMPT_RELEASE_RAGAS_GATE_ENABLED == "enforce"
            and not ragas.get("ragas_pass", False)
        ):
            blocked.append({
                "rule": str(ragas.get("rule", "ragas_gate")),
                "expected": str(ragas.get("expected", "RAGAS 达标（双门禁）")),
                "actual": str(ragas.get("actual", "RAGAS 未执行或未达标")),
                "severity": "error",
            })

        if blocked:
            raise PublishGateError(
                "发布门禁未通过（" + "；".join(b["rule"] for b in blocked) + "）——"
                "详见响应 blocked_rules 与 release metrics.gate",
                blocked_rules=blocked,
            )


def _load_prompt_service() -> Any:
    from backend.prompts.service import prompt_service

    return prompt_service


__all__ = ["PromptReleaseService"]
