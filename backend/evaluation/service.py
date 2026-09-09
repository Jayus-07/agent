"""EvaluationService — 唯一评估核心。

CLI / API / CI 均通过此服务执行评估，差异仅在 EvalConfig 配置。
"""
from __future__ import annotations

from typing import Any

from backend.evaluation.config import EvalConfig
from backend.evaluation.generation import get_token_usage, reset_token_usage
from backend.evaluation.gate import evaluate_tiers
from backend.evaluation.metrics import aggregate_metrics
from backend.evaluation.models import (
    EvalReport,
    EvalResult,
    ModuleKind,
    ModuleSummary,
    TestCase,
    TierSummary,
)
from backend.evaluation.registry import get_runner


def _filter_cases_by_tier(
    cases: list[TestCase], tier: str,
) -> list[TestCase]:
    """按 tier 过滤用例。tier="all" 返回全部。"""
    if tier == "all":
        return cases
    return [
        c for c in cases
        if (c.metadata.get("tier") or "core") == tier
    ]


def _skip_results(cases: list[TestCase], module: ModuleKind, reason: str) -> list[EvalResult]:
    return [
        EvalResult(
            case_id=c.id, module=module, status="skip",
            expected=c.expected, actual={}, metrics={}, error_msg=reason,
        )
        for c in cases
    ]


def _error_results(cases: list[TestCase], module: ModuleKind, error_msg: str) -> list[EvalResult]:
    return [
        EvalResult(
            case_id=c.id, module=module, status="error",
            expected=c.expected, actual={}, error_msg=error_msg,
        )
        for c in cases
    ]


def _build_summary(results: list[EvalResult], module: ModuleKind) -> ModuleSummary:
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skip")
    pass_rate = passed / max(total, 1)
    return ModuleSummary(
        module=module, total=total, passed=passed, failed=failed,
        errors=errors, skipped=skipped, pass_rate=round(pass_rate, 4),
        metrics=aggregate_metrics(results),
    )


def _inject_token_totals(summaries: list[ModuleSummary]) -> None:
    if not summaries:
        return
    usage = get_token_usage()
    if not any(v > 0 for v in usage.values()):
        return
    target = next((s for s in summaries if s.module == "rag"), summaries[0])
    target.metrics["total_prompt_tokens"] = usage["prompt_tokens"]
    target.metrics["total_completion_tokens"] = usage["completion_tokens"]


def _run_module(
    module: ModuleKind,
    cases: list[TestCase],
    live: bool = False,
    **kwargs: Any,
) -> list[EvalResult]:
    entry = get_runner(module)
    if entry is None:
        return _skip_results(cases, module, f"No runner registered for '{module}'")
    if entry.needs_live and not live:
        return _skip_results(cases, module, f"Module '{module}' requires --live mode")
    try:
        return entry.func(cases, live=live, **kwargs)
    except Exception as e:
        return _error_results(cases, module, str(e))


class EvaluationService:
    """评估服务 — 单一核心入口。"""

    def __init__(self) -> None:
        self._runners_registered = False

    def _ensure_runners(self) -> None:
        if self._runners_registered:
            return
        try:
            import backend.evaluation.runners_config  # noqa: F401
            self._runners_registered = True
        except ImportError:
            pass

    def evaluate(self, config: EvalConfig) -> EvalReport:
        """执行评估，返回 EvalReport。"""
        self._ensure_runners()
        reset_token_usage()

        from backend.evaluation.dataset import load_dataset, load_dataset_file

        live = config.live or config.judge

        if config.dataset:
            if config.selection:
                cases = load_dataset("rag", selection=config.selection)
            else:
                cases = load_dataset_file(config.dataset, default_module="rag")
            if config.smoke:
                cases = cases[:5]
            cases = _filter_cases_by_tier(cases, config.tier)
            results = _run_module("rag", cases, live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas, ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds)
            summaries = [_build_summary(results, "rag")]
            _inject_token_totals(summaries)
            return EvalReport(
                module="rag",
                mode="live" if live else "offline",
                smoke=config.smoke,
                tier=config.tier,
                summaries=summaries,
                results=list(results),
                total_score=None,
                tier_summaries=evaluate_tiers(cases, results),
            )

        module_kinds: list[ModuleKind] = (
            ["planner", "rag", "sql", "e2e"] if config.module == "all" else [config.module]  # type: ignore
        )

        all_results: list[EvalResult] = []
        summaries: list[ModuleSummary] = []
        all_cases: list[TestCase] = []

        for m in module_kinds:
            cases = load_dataset(m, selection=config.selection)
            if config.smoke:
                cases = cases[:5]
            cases = _filter_cases_by_tier(cases, config.tier)

            results = _run_module(m, cases, live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas, ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds)
            all_results.extend(results)
            summaries.append(_build_summary(results, m))
            all_cases.extend(cases)

        total_score = None
        if config.module == "all" and live:
            weights = {"planner": 0.15, "rag": 0.30, "sql": 0.25, "e2e": 0.30}
            score = 0.0
            for s in summaries:
                w = weights.get(s.module, 0.0)
                if s.module == "e2e" and "judge_total" in s.metrics:
                    module_score = s.metrics["judge_total"] / 5.0
                else:
                    module_score = s.pass_rate
                score += w * module_score
            total_score = round(score, 4)

        _inject_token_totals(summaries)
        return EvalReport(
            module=config.module,
            mode="live" if live else "offline",
            smoke=config.smoke,
            tier=config.tier,
            summaries=summaries,
            results=all_results,
            total_score=total_score,
            tier_summaries=evaluate_tiers(all_cases, all_results),
        )


_default_service = EvaluationService()


def evaluate(config: EvalConfig) -> EvalReport:
    """模块级快捷入口 — 使用默认 Service 实例。"""
    return _default_service.evaluate(config)
