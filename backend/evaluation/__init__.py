"""Evaluation Framework — 可移植的评估体系。

=== 框架层（零项目依赖，可复制到任何项目）===

    from backend.evaluation import (
        # 数据模型
        TestCase, EvalResult, EvalReport, ModuleSummary, ModuleKind,
        # 指标
        recall_at_k, mrr, ndcg_at_k, jaccard_similarity, exact_match, result_set_match,
        # 注册表
        register_runner, get_runner, list_registered,
        # 报告
        print_summary, write_markdown_report,
        # 调度
        run_all,
    )

=== 在新项目中使用 ===

1. 复制 evaluation/ 目录到新项目
2. 删除 evaluation/runners/builtin.py（旧项目的 runner）
3. 创建自己的 runner 注册脚本 my_eval_runners.py：

    from backend.evaluation import register_runner, TestCase, EvalResult

    def my_runner(cases: list[TestCase], **kwargs) -> list[EvalResult]:
        # 对接自己的系统...
        ...

    register_runner("rag", my_runner, needs_live=False)
    # ... 注册其他模块

4. 运行：
    python -m evaluation --runner-config my_eval_runners --smoke
"""

# 数据模型
from backend.evaluation.models import (
    TestCase, EvalResult, EvalReport, ModuleSummary,
    ModuleKind, RunnerFunc, RunnerEntry,
)

# 指标
from backend.evaluation.metrics import (
    recall_at_k, mrr, ndcg_at_k, jaccard_similarity,
    exact_match, result_set_match,
    # V1.0 新增
    chunk_recall_at_k, p95_latency, reject_accuracy,
    stability_variance, aggregate_metrics,
)

# 注册表
from backend.evaluation.registry import register_runner, get_runner, list_registered

# 报告
from backend.evaluation.report import print_summary, write_markdown_report, write_json_report

# 调度
from backend.evaluation.runner import run_all, evaluate_planner_offline

# 数据集
from backend.evaluation.dataset import load_dataset, validate_dataset, select_cases

# LLM-as-Judge
from backend.evaluation.judge import (
    JudgeResult, judge_answer, build_judge_prompt,
)

# V1.0 重构新增
from backend.evaluation.storage import (
    persist_report, load_report, list_runs,
    get_git_sha, get_dataset_version, make_run_id,
)

# 统一门禁（Phase 2: 合并 baseline + gate 逻辑）
from backend.evaluation.gate import (
    TIER_THRESHOLDS, evaluate_tiers, flag_regressions,
    promote, load_baseline, diff_baseline, check_regression,
)

# 配置 + 服务（Phase 3: 单一核心）
from backend.evaluation.config import EvalConfig
from backend.evaluation.service import EvaluationService

# Evaluator Provider（Phase 3: 可组合指标层）
from backend.evaluation.evaluators import (
    Evaluator, SemanticEvaluator,
)

__all__ = [
    # Models
    "TestCase", "EvalResult", "EvalReport", "ModuleSummary",
    "ModuleKind", "RunnerFunc", "RunnerEntry",
    # Metrics
    "recall_at_k", "mrr", "ndcg_at_k", "jaccard_similarity",
    "exact_match", "result_set_match",
    "chunk_recall_at_k", "p95_latency", "reject_accuracy",
    "stability_variance", "aggregate_metrics",
    # Registry
    "register_runner", "get_runner", "list_registered",
    # Report
    "print_summary", "write_markdown_report", "write_json_report",
    # Runner
    "run_all", "evaluate_planner_offline",
    # Dataset
    "load_dataset", "validate_dataset", "select_cases",
    # Judge
    "JudgeResult", "judge_answer", "build_judge_prompt",
    # Storage (V1.0)
    "persist_report", "load_report", "list_runs",
    "get_git_sha", "get_dataset_version", "make_run_id",
    # Gate (Phase 2: 统一门禁)
    "TIER_THRESHOLDS", "evaluate_tiers", "flag_regressions",
    "promote", "load_baseline", "diff_baseline", "check_regression",
    # Config + Service (Phase 3: 单一核心)
    "EvalConfig", "EvaluationService",
    # Evaluator Providers (Phase 3: 可组合指标层)
    "Evaluator", "SemanticEvaluator",
]
