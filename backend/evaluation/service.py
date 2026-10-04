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
    ALL_RUN_MODULES,
    EvalReport,
    EvalResult,
    ModuleKind,
    ModuleSummary,
    TestCase,
    TierSummary,
)
from backend.evaluation.registry import get_runner


class RunAlreadyFinalizedError(ValueError):
    """C2-2/RUN-02：对已终态 run 的隐式重跑被拒绝（CLI/API 转 409）。"""


class BudgetBlockedError(RuntimeError):
    """C5-6/COST-10：租户预算超限，评测 run 拒绝启动。"""


def _current_actor() -> str:
    import os as _os

    return _os.getenv("EVAL_TRIGGERED_BY", "") or "unknown"


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


def _resolve_rag_scope(cases: list[TestCase], config: EvalConfig):
    """从 suite 案例解析并校验唯一 RAG 运行范围。"""
    from backend.evaluation.runners.rag import build_eval_scope

    case_kbs = {str(case.metadata.get("kb_id", "")) for case in cases}
    case_fixture_sets = {str(case.metadata.get("fixture_set", "")) for case in cases}
    if len(case_kbs) != 1 or len(case_fixture_sets) != 1:
        raise ValueError(
            "RAG 评测必须通过显式 suite 解析唯一 kb_id/fixture_set；"
            f"实际 kb_id={sorted(case_kbs)}, fixture_set={sorted(case_fixture_sets)}"
        )
    kb_id = config.kb_id or next(iter(case_kbs))
    fixture_set = config.fixture_set or next(iter(case_fixture_sets))
    if kb_id != next(iter(case_kbs)):
        raise ValueError(f"配置 kb_id={kb_id} 与 suite kb_id={next(iter(case_kbs))} 不一致")
    if fixture_set != next(iter(case_fixture_sets)):
        raise ValueError(
            f"配置 fixture_set={fixture_set} 与 suite fixture_set={next(iter(case_fixture_sets))} 不一致"
        )
    scope = build_eval_scope(
        kb_id=kb_id,
        fixture_set=fixture_set,
        multiquery=config.multiquery,
    )
    suite_version = str(cases[0].metadata.get("dataset_version", "")) if cases else ""
    if config.dataset_version and suite_version and config.dataset_version != suite_version:
        raise ValueError(
            f"配置 dataset_version={config.dataset_version} 与 suite={suite_version} 不一致"
        )
    return scope, config.dataset_version or suite_version


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
        from backend.evaluation.generation import (
            get_evaluator_token_usage,
            get_token_usage,
        )

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

            # evaluator 侧（judge 走项目 LLM、RAGAS 走独立云 LLM，均不经过
            # JSONL tracker）内存计数器合并进 evaluator.judge
            evaluator_usage = get_evaluator_token_usage()
            evaluator_tokens["judge"] += int(evaluator_usage.get("prompt_tokens", 0)) \
                + int(evaluator_usage.get("completion_tokens", 0))

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


def _evaluator_mode(config: EvalConfig) -> str:
    """RAGAS-01/02：评估器模式标记，随 report/metadata 与 meta.json 落盘。

    自研指标恒执行；RAGAS 批量默认开启（rag.py 按 no_ragas 关闭），
    因此只有 self（显式 --no-ragas）与 self+ragas（默认/显式 --ragas）两态。
    """
    if config.no_ragas:
        return "self"
    return "self+ragas"


def _build_buckets(
    cases: list[TestCase], results: list[EvalResult],
) -> dict[str, Any]:
    """SELF-06：按 domain / difficulty / query_type 分桶统计通过情况。

    元数据缺失的用例归入 unknown 桶——不臆造分组，也不虚构 100%。
    """
    meta_by_case = {c.id: (c.metadata or {}) for c in cases}
    key_map = {
        "by_domain": "domain",
        "by_difficulty": "difficulty",
        "by_query_type": "query_type",
    }
    buckets: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in key_map}
    for r in results:
        meta = meta_by_case.get(r.case_id, {})
        for bucket_name, meta_key in key_map.items():
            value = str(meta.get(meta_key) or "unknown")
            slot = buckets[bucket_name].setdefault(
                value,
                {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "pass_rate": 0.0},
            )
            slot["total"] += 1
            if r.status == "pass":
                slot["passed"] += 1
            elif r.status == "fail":
                slot["failed"] += 1
            elif r.status == "error":
                slot["errors"] += 1
            else:
                slot["skipped"] += 1
    for bucket in buckets.values():
        for slot in bucket.values():
            if slot["total"]:
                slot["pass_rate"] = round(slot["passed"] / slot["total"], 4)
    return buckets


def _ragas_sample_stats(results: list[EvalResult]) -> dict[str, int]:
    """RAGAS-10：RAGAS 批量样本口径——valid=至少产出一项 ragas 分值。

    进了批量但没拿到分值（Judge 失败/超时/字段缺失）计 invalid，不得把
    invalid 混进 valid 凑通过率。
    """
    valid = invalid = 0
    for r in results:
        metrics = r.metrics or {}
        has_value = any(
            k.startswith("ragas_") and k != "ragas_reason" and isinstance(v, (int, float))
            for k, v in metrics.items()
        )
        has_attempt = any(k.startswith("ragas_") for k in metrics)
        if has_value:
            valid += 1
        elif has_attempt:
            invalid += 1
    return {"valid": valid, "invalid": invalid}


# C4-7：RAGAS 有效样本率低于该值 → 报告标 ragas_degraded（C1-7 门据此 block）
RAGAS_VALID_RATIO_MIN = 0.90


def _ragas_degraded(ragas_samples: dict[str, int]) -> bool:
    attempted = int(ragas_samples.get("valid", 0)) + int(ragas_samples.get("invalid", 0))
    if attempted <= 0:
        return False  # 未执行不算 degraded（由 ragas_not_executed 口径负责）
    ratio = int(ragas_samples.get("valid", 0)) / attempted
    return ratio < RAGAS_VALID_RATIO_MIN


def _attach_evaluator_cost(
    metadata: dict[str, Any], summaries: list[ModuleSummary],
) -> None:
    """C4-9/RAGAS-14：evaluator token × 价格 → 估算成本（CNY）。

    价格不可得时记 ``{"cost_cny": None, "basis": "unavailable_price"}``，
    前端据此渲染 unavailable 而非 ¥0（COST-09 口径延续）。
    """
    from backend.evaluation.evaluator_cost import estimate_evaluator_cost_cny

    rag_summary = next((s for s in summaries if s.module == "rag"), None)
    if rag_summary is None:
        return
    token_summary = (rag_summary.metrics or {}).get("token_summary")
    estimate = estimate_evaluator_cost_cny(token_summary)
    if estimate is not None:
        metadata["evaluator_cost"] = estimate


def _strict_fields_enabled() -> bool:
    import os as _os

    return _os.getenv("EVAL_STRICT_FIELDS", "").strip().lower() in ("1", "true", "yes")


def _strict_fields_check(cases: list[TestCase]) -> None:
    """C9-2/P0-03：严格字段校验（EVAL_STRICT_FIELDS=1 或 config.strict_fields）。

    缺 question/expected_answer/ground_truth 的样本阻止启动并逐条列出
    缺失字段；缺 answer/contexts 的 RAGAS 依赖在样本级降级（历史语义），
    但发布评测必须全量可用——strict 下不接受静默降级。
    """
    import os as _os

    problems: list[str] = []
    for case in cases:
        missing: list[str] = []
        if not str(case.question or "").strip():
            missing.append("question")
        expected = case.expected or {}
        if not str(expected.get("expected_answer", "") or "").strip():
            missing.append("expected_answer")
        if not str(expected.get("ground_truth", "") or "").strip():
            missing.append("ground_truth")
        if missing:
            problems.append(f"{case.id}: 缺 {', '.join(missing)}")
    if problems:
        preview = "；".join(problems[:10])
        more = f"（共 {len(problems)} 条）" if len(problems) > 10 else ""
        raise StrictFieldValidationError(
            f"严格字段校验失败，拒绝启动：{preview}{more}。"
            f"发布评测要求 question/expected_answer/ground_truth 全量可用；"
            f"请修正样本或改用非 strict 模式（仅限本地调试）"
        )


class StrictFieldValidationError(ValueError):
    """C9-2：严格字段校验未通过（阻止启动）。"""


def _snapshot_suite_mtimes(config: EvalConfig) -> dict[str, float]:
    """C9-4/CON-05：运行开始时记录 suite 相关文件的 mtime。"""
    from backend.evaluation.dataset.loader import DATASET_DIR

    paths: list = []
    if config.selection:
        paths.append(DATASET_DIR / "rag" / "suites" / f"{config.selection}.json")
    paths.append(DATASET_DIR / "rag" / "cases.jsonl")
    mtimes: dict[str, float] = {}
    for path in paths:
        try:
            if path.exists():
                mtimes[str(path.name)] = path.stat().st_mtime
        except OSError:
            continue
    return mtimes


def _check_suite_mtime(baseline: dict[str, float]) -> str:
    """运行结束时比对；变化即告警（run 内一致性不受影响——内存态已固定）。"""
    if not baseline:
        return ""
    from backend.evaluation.dataset.loader import DATASET_DIR

    changed: list[str] = []
    for name, mtime in baseline.items():
        path = DATASET_DIR / "rag" / "suites" / name
        if not path.exists():
            path = DATASET_DIR / "rag" / name
        try:
            if not path.exists() or path.stat().st_mtime != mtime:
                changed.append(name)
        except OSError:
            continue
    if changed:
        message = (
            f"评测运行期间 suite 文件被修改：{', '.join(changed)}——"
            f"本次 run 结果基于启动时加载的内存态（一致性不受影响），"
            f"但下次运行将使用新内容；请为修改后的数据创建新版本（DATA-01）"
        )
        logger.warning("[service] %s", message)
        return message
    return ""


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
        """执行评估，并在整个运行期间固定候选 Prompt 快照。"""
        from contextlib import nullcontext

        if config.prompt_versions:
            from backend.prompts.service import prompt_service

            prompt_context = prompt_service.bind_prompt_versions(config.prompt_versions)
        else:
            prompt_context = nullcontext()
        with prompt_context:
            return self._evaluate(config)

    def _evaluate(self, config: EvalConfig) -> EvalReport:
        """执行评估主流程。"""
        import threading
        import time as _time
        from backend.evaluation.storage import (
            HEARTBEAT_INTERVAL_SECONDS,
            is_cancel_requested,
            make_run_id,
            mark_run_status,
            read_run_status,
            touch_run_heartbeat,
        )

        self._ensure_runners()
        reset_token_usage()
        # C5-6/COST-10：预算阻断（灰度开关默认关）。开启后 run 启动前查
        # 租户预算窗口，超限拒绝启动（fail-closed）——预算不允许「先跑后算」。
        from backend.evaluation import run_guards as _rg

        if _rg.BUDGET_BLOCK_ENABLED:
            budget_reason = _rg.RunGuard(
                run_id or "pre-start", actor=_current_actor(),
            ).check_budget()
            if budget_reason:
                raise BudgetBlockedError(
                    f"租户 {_rg.BUDGET_TENANT_ID} 预算超限，评测 run 拒绝启动"
                    f"（reason={budget_reason}，COST-10 fail-closed）"
                )
        # 运行一开始就固定 run_id：checkpoint 与最终报告使用同一目录；
        # 中断后可从目录名取得 ID，再通过 --run-id + 默认 resume 续跑。
        run_id = config.run_id or make_run_id()

        # C2-2/RUN-02：终态 run 拒绝隐式重跑。显式 resume（同 run_id 断点
        # 续跑）或 force_rerun（全量重跑）才放行；默认参数撞上终态 run 视为
        # 调用方失误，抛错由 CLI/API 转 409——防止 completed 结果被静默改写。
        if config.run_id:
            existing = read_run_status(config.run_id) or {}
            if existing.get("status") in ("completed", "failed", "cancelled"):
                resume_explicit = "resume" in config.model_fields_set and config.resume
                if config.force_rerun:
                    from backend.evaluation.audit import record_operation

                    record_operation(
                        "eval_run.force_rerun", config.run_id,
                        actor=_current_actor(),
                        detail=f"previous_status={existing['status']}",
                    )
                elif not resume_explicit:
                    raise RunAlreadyFinalizedError(
                        f"run {config.run_id} 已终态（{existing['status']}），"
                        f"拒绝隐式重跑；续跑请显式 --resume，全量重跑请 --force"
                    )

        # token 统计时间窗起点（JSONL 过滤用，防止跨 run 累计污染）
        run_started_ts = _time.time()
        # RUN-01：运行即登记 running（断点续跑同 run_id 刷新 started_at，
        # attempt_no +1——C2-3 失败重试语义显式化）；进程被杀不会收口终态，
        # 由 read_run_status 的 stale 判定兜底（RUN-07/08）
        mark_run_status(run_id, "running")
        # C2-5/RUN-08：心跳线程——长跑期间周期性 touch heartbeat_at，
        # stale 判定从「6h 无进展」细化到「10min 无心跳」；主流程结束
        # （含异常）先停线程再收口终态，避免心跳复活终态。
        heartbeat_stop = threading.Event()

        def _heartbeat() -> None:
            while not heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                touch_run_heartbeat(run_id)

        heartbeat_thread = threading.Thread(
            target=_heartbeat, name=f"eval-heartbeat-{run_id}", daemon=True,
        )
        heartbeat_thread.start()
        try:
            report = self._evaluate_cases(config, run_id, run_started_ts)
            # C2-3：attempt 计数随报告落盘（跨次续跑可审计「跑了几次」）
            status = read_run_status(run_id) or {}
            report.metadata["attempt_no"] = int(status.get("attempt_no", 1) or 1)
            # 五期：守卫配置与实际触发原因随报告落盘（审计与排查依据）
            from backend.evaluation import run_guards as _guards

            guard_meta = _guards.guard_config_snapshot()
            guard_meta["stop_reason"] = str(
                status.get("error", "")
                or ("cancelled" if is_cancel_requested(run_id) else "")
            )
            report.metadata["run_guards"] = guard_meta
            return report
        except Exception as exc:
            mark_run_status(run_id, "failed", error=str(exc))
            raise
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            # 等待期间命中取消请求 → 终态补收口为 cancelled（RUN-03）
            if is_cancel_requested(run_id):
                from backend.evaluation.storage import read_run_status as _rrs

                if (_rrs(run_id) or {}).get("status") == "running":
                    mark_run_status(run_id, "cancelled")

    def _evaluate_cases(
        self,
        config: EvalConfig,
        run_id: str,
        run_started_ts: float,
    ) -> EvalReport:
        """执行各模块评估并装配报告（生命周期状态由 _evaluate 管理）。"""
        from backend.evaluation.dataset import load_dataset, load_dataset_file
        from backend.evaluation.dataset.loader import load_suite_config

        live = config.live or config.judge

        # GATE-12：suite 级最低样本量配置（suite JSON 声明优先，缺省全局口径）
        suite_governance = (
            load_suite_config("rag", config.selection) if config.selection else {}
        )
        min_samples = suite_governance.get("min_samples")
        min_valid_samples = suite_governance.get("min_valid_samples")
        # C9-4/CON-05：运行期 suite 文件 mtime 基线（结束时比对告警）
        suite_mtime_baseline = _snapshot_suite_mtimes(config)

        if config.dataset:
            if config.selection:
                cases = load_dataset("rag", selection=config.selection)
            else:
                cases = load_dataset_file(config.dataset, default_module="rag")
            if config.smoke:
                cases = cases[:5]
            cases = _filter_cases_by_tier(cases, config.tier)
            if config.strict_fields or _strict_fields_enabled():
                _strict_fields_check(cases)
            scope, dataset_version = _resolve_rag_scope(cases, config)
            results = _run_module("rag", cases, live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas, ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds, workers=config.workers, ragas_workers=config.ragas_workers, resume=config.resume, multiquery=config.multiquery, full_trace=config.full_trace, eval_scope=scope, run_id=run_id)
            summaries = [_build_summary(results, "rag")]
            _inject_token_totals(summaries, run_started_ts)
            # C4-1/C4-2/C7-4：judge/RAGAS 调用参数快照（含 seed_support 显式口径）
            from backend.evaluation.evaluator_config import (
                collect_judge_config,
                collect_ragas_config,
            )

            dataset_report_metadata = {
                "evaluation_scope": scope.as_dict(),
                "dataset_version": dataset_version,
                "selection": config.selection,
                "run_id": run_id,
                "evaluator_mode": _evaluator_mode(config),
                "buckets": _build_buckets(cases, results),
                "ragas_samples": _ragas_sample_stats(results),
                "suite_governance": suite_governance,
            }
            dataset_report_metadata["ragas_degraded"] = _ragas_degraded(
                dataset_report_metadata["ragas_samples"],
            )
            # C9-4/CON-05：运行期 suite 文件改动告警（如有）
            suite_mtime_note = _check_suite_mtime(suite_mtime_baseline)
            if suite_mtime_note:
                dataset_report_metadata["suite_mtime_warning"] = suite_mtime_note
            if not config.no_ragas:
                dataset_report_metadata["ragas_config"] = collect_ragas_config(
                    config.ragas_level,
                )
            if config.judge:
                dataset_report_metadata["judge_config"] = collect_judge_config()
            _attach_evaluator_cost(dataset_report_metadata, summaries)
            report = EvalReport(
                module="rag",
                mode="live" if live else "offline",
                smoke=config.smoke,
                tier=config.tier,
                summaries=summaries,
                results=list(results),
                total_score=None,
                tier_summaries=evaluate_tiers(
                    cases, results,
                    min_samples=min_samples, min_valid_samples=min_valid_samples,
                ),
                metadata=dataset_report_metadata,
            )
            return _attach_provenance(config, report)

        # 模块清单派生自 models.ModuleKind（唯一事实源）；`all` 的取/舍口径见
        # models.ALL_RUN_MODULES（排除项逐个带理由），此处不再手写列表
        module_kinds: list[ModuleKind] = (
            list(ALL_RUN_MODULES) if config.module == "all" else [config.module]  # type: ignore
        )

        all_results: list[EvalResult] = []
        summaries: list[ModuleSummary] = []
        all_cases: list[TestCase] = []
        report_metadata: dict[str, Any] = {"run_id": run_id}

        for m in module_kinds:
            cases = load_dataset(m, selection=config.selection)
            if config.smoke:
                cases = cases[:5]
            cases = _filter_cases_by_tier(cases, config.tier)
            if config.strict_fields or _strict_fields_enabled():
                _strict_fields_check(cases)

            runner_kwargs = dict(
                live=live, judge=config.judge, ragas=config.ragas, no_ragas=config.no_ragas,
                ragas_level=config.ragas_level, semantic_thresholds=config.semantic_thresholds,
                workers=config.workers, ragas_workers=config.ragas_workers, resume=config.resume,
                multiquery=config.multiquery, full_trace=config.full_trace,
            )
            if m == "rag":
                scope, dataset_version = _resolve_rag_scope(cases, config)
                runner_kwargs.update(eval_scope=scope, run_id=run_id)
                report_metadata["evaluation_scope"] = scope.as_dict()
                report_metadata["dataset_version"] = dataset_version
            results = _run_module(m, cases, **runner_kwargs)
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
        report_metadata["evaluator_mode"] = _evaluator_mode(config)
        report_metadata["buckets"] = _build_buckets(all_cases, all_results)
        ragas_stats = _ragas_sample_stats(all_results)
        report_metadata["ragas_samples"] = ragas_stats
        report_metadata["ragas_degraded"] = _ragas_degraded(ragas_stats)
        suite_mtime_note = _check_suite_mtime(suite_mtime_baseline)
        if suite_mtime_note:
            report_metadata["suite_mtime_warning"] = suite_mtime_note
        # C4-1/C4-2/C7-4：judge/RAGAS 调用参数快照（含 seed_support 显式口径）
        from backend.evaluation.evaluator_config import (
            collect_judge_config,
            collect_ragas_config,
        )

        if not config.no_ragas:
            report_metadata["ragas_config"] = collect_ragas_config(config.ragas_level)
        if config.judge:
            report_metadata["judge_config"] = collect_judge_config()
        _attach_evaluator_cost(report_metadata, summaries)
        if suite_governance:
            report_metadata["suite_governance"] = suite_governance
        report = EvalReport(
            module=config.module,
            mode="live" if live else "offline",
            smoke=config.smoke,
            tier=config.tier,
            summaries=summaries,
            results=all_results,
            total_score=total_score,
            tier_summaries=evaluate_tiers(
                all_cases, all_results,
                min_samples=min_samples, min_valid_samples=min_valid_samples,
            ),
            metadata=report_metadata,
        )
        return _attach_provenance(config, report)


_default_service = EvaluationService()


def _attach_provenance(config: EvalConfig, report: EvalReport) -> EvalReport:
    """把 scope、候选 Prompt 和发布关联写入 report metadata。"""
    from backend.evaluation.provenance import (
        build_eval_provenance,
        build_snapshot_hash,
        collect_snapshot_inputs,
    )

    if config.prompt_versions:
        report.prompt_versions = {
            key: _coerce_prompt_version(value)
            for key, value in config.prompt_versions.items()
        }
    elif not report.prompt_versions:
        try:
            from backend.prompts.service import snapshot_prompt_versions

            report.prompt_versions = snapshot_prompt_versions()
        except Exception:
            report.prompt_versions = {}
    report.metadata["eval_provenance"] = build_eval_provenance(config, report)
    # C3-3/REPRO-09：统一快照哈希——所有关键输入的确定性摘要，
    # meta.json / DB 列 / trace 消费同一值（REPRO-10 守护见测试）
    try:
        snapshot_inputs = collect_snapshot_inputs(config, report)
        report.metadata["evaluation_snapshot_inputs"] = snapshot_inputs
        report.metadata["evaluation_snapshot_hash"] = build_snapshot_hash(snapshot_inputs)
    except Exception as e:  # noqa: BLE001 — 哈希失败诚实留痕，不伪造
        report.metadata["evaluation_snapshot_hash"] = ""
        report.metadata["evaluation_snapshot_error"] = str(e)[:200]
    return report


def _coerce_prompt_version(value: int | str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def evaluate(config: EvalConfig) -> EvalReport:
    """模块级快捷入口 — 使用默认 Service 实例。"""
    return _default_service.evaluate(config)
