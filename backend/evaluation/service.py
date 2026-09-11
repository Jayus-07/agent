"""EvaluationService — 唯一评估核心。

CLI / API / CI 均通过此服务执行评估，差异仅在 EvalConfig 配置。
P0: Evaluation Token Tracking with SUT vs Evaluator separation
"""
from __future__ import annotations

import os
from typing import Any

from backend.config import EVAL_DATASET_PATH, TOKEN_USAGE_LOG_PATH
from backend.evaluation.config import EvalConfig
from backend.evaluation.gate import evaluate_tiers
from backend.evaluation.metrics import aggregate_metrics
from backend.evaluation.generation import reset_token_usage
from backend.shared.logger import logger
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


def _inject_token_totals(summaries: list[ModuleSummary], run_started_ts: float | None = None) -> None:
    """P0: Inject token statistics with SUT vs Evaluator separation.

    Structure:
      - RAG module → SUT tokens (embedding, rerank, answer_llm)
      - RAGAS metrics → Evaluator tokens (judge llm calls)

    run_started_ts: 本次评测开始时间（epoch 秒）。JSONL 记录按该时间窗过滤，
    原实现累加整个历史日志文件（无轮转时第 N 次评测是历次累计值）。
    """
    if not summaries:
        return

    # Try to read from JSONL (if token tracker is enabled)
    try:
        import os
        import json
        import time as _time
        from datetime import datetime, timezone

        from backend.config import TOKEN_USAGE_LOG_PATH
        from backend.evaluation.generation import get_token_usage

        log_path = os.path.expanduser(TOKEN_USAGE_LOG_PATH)
        if os.path.exists(log_path):
            sut_tokens = {"embedding": 0, "rerank": 0, "answer_llm": 0}
            evaluator_tokens = {"judge": 0}

            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                        # 按 run 时间窗过滤：只统计本次评测期间的记录
                        if run_started_ts is not None:
                            ts_raw = record.get("timestamp", "")
                            try:
                                ts = datetime.fromisoformat(
                                    str(ts_raw).replace("Z", "+00:00"),
                                )
                                if ts.timestamp() < run_started_ts:
                                    continue
                            except (ValueError, TypeError, OverflowError):
                                continue
                        component = record.get("component", "")
                        total = record.get("total_tokens") or 0

                        if component == "embedding":
                            sut_tokens["embedding"] += total
                        elif component == "rerank":
                            sut_tokens["rerank"] += total
                        elif component == "llm":
                            # runtime 埋点的 llm 记录无 evaluation_run_id，
                            # 计入 SUT 侧 runtime LLM（检索改写等）；带 run_id
                            # 的为评测器（judge/RAGAS）调用
                            if record.get("evaluation_run_id"):
                                evaluator_tokens["judge"] += total
                            else:
                                sut_tokens["answer_llm"] += total
                    except (json.JSONDecodeError, KeyError):
                        continue

            # 本地 Ollama 生成计数（evaluation/generation 内存计数器，
            # 不经过 JSONL tracker）合并进 answer_llm
            local_usage = get_token_usage()
            sut_tokens["answer_llm"] += int(local_usage.get("prompt_tokens", 0)) \
                + int(local_usage.get("completion_tokens", 0))

            # Find RAG summary and inject comprehensive token stats
            rag_summary = next((s for s in summaries if s.module == "rag"), None)
            if rag_summary:
                rag_summary.metrics["token_summary"] = {
                    "sut": {
                        "embedding": sut_tokens["embedding"],
                        "rerank": sut_tokens["rerank"],
                        "answer_llm": sut_tokens["answer_llm"],
                        "total": sum(sut_tokens.values()),
                    },
                    "evaluator": {
                        "judge": evaluator_tokens["judge"],
                        "total": evaluator_tokens["judge"],
                    },
                    "grand_total": sum(sut_tokens.values()) + evaluator_tokens["judge"],
                    "window": "since_run_start" if run_started_ts is not None else "all_time",
                }

    except Exception as e:
        # Soft failure: don't break evaluation if token tracking fails
        logger.debug(f"[TokenStats] Failed to aggregate tokens: {e}")


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
        import time as _time
        self._ensure_runners()
        reset_token_usage()
        # token 统计时间窗起点（JSONL 过滤用，防止跨 run 累计污染）
        run_started_ts = _time.time()

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
            results = _run_module("rag", cases, live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas, ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds, workers=config.workers, ragas_workers=config.ragas_workers, resume=config.resume)
            summaries = [_build_summary(results, "rag")]
            _inject_token_totals(summaries, run_started_ts)
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

            results = _run_module(m, cases, live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas, ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds, workers=config.workers, ragas_workers=config.ragas_workers, resume=config.resume)
            all_results.extend(results)
            summaries.append(_build_summary(results, m))
            all_cases.extend(cases)

        total_score = None
        if config.module == "all" and live:
            weights = {"planner": 0.20, "rag": 0.45, "sql": 0.15, "e2e": 0.20}
            score = 0.0
            for s in summaries:
                w = weights.get(s.module, 0.0)
                score += w * s.pass_rate
            total_score = round(score, 4)

        _inject_token_totals(summaries, run_started_ts)
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
