"""Prompt 发布门禁使用的本地评测适配器。"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any

from backend.evaluation.config import EvalConfig
from backend.evaluation.service import EvaluationService
from backend.evaluation.storage import persist_report
from backend.infra.llm.registry_store import refresh_registry
from backend.prompts.release_models import PromptReleaseRecord


async def run_prompt_release_evaluation(
    release: PromptReleaseRecord,
) -> dict[str, Any]:
    """用数据库绑定的外部模型执行发布评测并返回门禁结果。"""
    return await asyncio.to_thread(_run_sync, release)


def _run_sync(release: PromptReleaseRecord) -> dict[str, Any]:
    with _evaluation_env(release.created_by):
        asyncio.run(refresh_registry())
        report = EvaluationService().evaluate(
            EvalConfig(
                module="rag",
                live=True,
                selection=release.eval_suite,
                dataset_version=str(
                    release.dataset_provenance.get("version")
                    or release.dataset_provenance.get("dataset_version")
                    or ""
                ),
                prompt_versions=release.prompt_snapshot,
                release_id=release.release_id,
            )
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


__all__ = ["run_prompt_release_evaluation"]
