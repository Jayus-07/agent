"""评估执行引擎 — 基于注册表的通用调度器。

可移植性：此文件的 run_all() / _build_summary() / evaluate_planner_offline()
是通用逻辑，零项目依赖。具体的 runner 函数通过 registry 注册，
复制到新项目后只需注册自己的 runner 即可。
"""

import time
from typing import Any
from backend.evaluation.models import (
    TestCase, EvalResult, ModuleKind, EvalReport, ModuleSummary,
)
from backend.evaluation.metrics import recall_at_k, mrr, ndcg_at_k, jaccard_similarity
from backend.evaluation.registry import get_runner, list_registered


# ==================== Planner 离线评估（通用，不依赖项目） ====================

def evaluate_planner_offline(
    case_id: str,
    expected: dict[str, Any],
    actual_capabilities: list[str],
) -> EvalResult:
    """离线评估 Planner 输出 — 纯函数，不依赖项目模块。

    评估维度：
    - jaccard: 期望能力与实际能力的 Jaccard 相似度
    - redundancy: 不应出现的能力实际出现的比例
    - structure_ok: edges 中的能力是否都出现在实际能力中
    """
    expected_caps = set(expected.get("capabilities", []))
    should_not = set(expected.get("should_not_contain", []))
    actual_set = set(actual_capabilities)

    jaccard = jaccard_similarity(actual_set, expected_caps)

    redundancy_hits = should_not & actual_set
    redundancy = len(redundancy_hits) / len(should_not) if should_not else 0.0

    structure_ok = True
    if "edges" in expected:
        edge_caps = set()
        for edge in expected["edges"]:
            edge_caps.add(edge["from"])
            edge_caps.add(edge["to"])
        structure_ok = edge_caps.issubset(actual_set)

    passed = (
        jaccard >= 0.5
        and redundancy <= 0.25
        and structure_ok
    )

    return EvalResult(
        case_id=case_id,
        module="planner",
        status="pass" if passed else "fail",
        expected=expected,
        actual={"capabilities": actual_capabilities},
        metrics={
            "jaccard": round(jaccard, 4),
            "redundancy": round(redundancy, 4),
            "structure_ok": 1.0 if structure_ok else 0.0,
        },
    )


# ==================== 辅助 ====================

def _skip_results(cases: list[TestCase], module: ModuleKind, reason: str) -> list[EvalResult]:
    """生成 skip 状态的结果列表。"""
    return [
        EvalResult(
            case_id=c.id, module=module, status="skip",
            expected=c.expected, actual={}, metrics={}, error_msg=reason,
        )
        for c in cases
    ]


def _error_results(cases: list[TestCase], module: ModuleKind, error_msg: str) -> list[EvalResult]:
    """生成 error 状态的结果列表。"""
    return [
        EvalResult(
            case_id=c.id, module=module, status="error",
            expected=c.expected, actual={}, error_msg=error_msg,
        )
        for c in cases
    ]


def _build_summary(results: list[EvalResult], module: ModuleKind) -> ModuleSummary:
    """从结果列表构建模块汇总。"""
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skip")
    pass_rate = passed / max(total, 1)

    agg_metrics: dict[str, float] = {}
    metric_keys: set[str] = set()
    for r in results:
        metric_keys.update(r.metrics.keys())
    for key in metric_keys:
        values = [r.metrics[key] for r in results if key in r.metrics]
        if values:
            agg_metrics[key] = round(sum(values) / len(values), 4)

    return ModuleSummary(
        module=module, total=total, passed=passed, failed=failed,
        errors=errors, skipped=skipped, pass_rate=round(pass_rate, 4),
        metrics=agg_metrics,
    )


_runners_registered = False


def _ensure_runners_registered() -> None:
    """V1.0: 确保 runner 已注册。

    CLI 通过 --runner-config 显式导入注册；run_all() 自动调用此函数，
    尝试导入默认 runners_config（backend.evaluation.runners_config）。
    """
    global _runners_registered
    if _runners_registered:
        return
    try:
        import backend.evaluation.runners_config  # noqa: F401
        _runners_registered = True
    except ImportError:
        pass  # 纯净框架允许 skip


# ==================== 通用调度器 ====================

def run_module(
    module: ModuleKind,
    cases: list[TestCase],
    live: bool = False,
    **kwargs: Any,
) -> list[EvalResult]:
    """运行单个模块的评估 — 通过注册表查找 runner 并执行。

    这是核心调度函数。如果模块未注册或需要 live 但未启用，返回 skip。

    Args:
        module: 模块标识
        cases: 测试用例列表
        live: 是否启用真实 LLM 调用
        **kwargs: 传递给 runner 的额外参数（如 judge=True）

    Returns:
        EvalResult 列表，每个用例一个结果
    """
    if not cases:
        return []

    entry = get_runner(module)
    if entry is None:
        return _skip_results(cases, module, f"No runner registered for '{module}'")

    if entry.needs_live and not live:
        return _skip_results(cases, module, f"Module '{module}' requires --live mode")

    try:
        return entry.func(cases, **kwargs)
    except Exception as e:
        return _error_results(cases, module, str(e))


def run_all(
    module: str = "all",
    live: bool = False,
    smoke: bool = False,
    judge: bool = False,
    dataset_file: str | None = None,
) -> EvalReport:
    """主入口：运行一个或多个模块的评估，返回 EvalReport。

    Args:
        module: "all" | "planner" | "rag" | "sql" | "e2e"
        live: 是否启用真实 LLM 调用
        smoke: 快速冒烟（每模块取前 5 条）
        judge: 是否启用 LLM-as-Judge（传递给 E2E runner）
        dataset_file: 自定义评测集文件名（如 "rag_test_kb.json"），
                      指定时用 rag runner 跑该评测集，忽略 module

    Returns:
        EvalReport: 包含所有模块的汇总和详细结果
    """
    # V1.0: 自动触发 runner 注册（CLI 通过 _bootstrap_runners 注册，
    # 但直接 import 调 run_all() 时不会，缺少这一步会全部走 skip）
    _ensure_runners_registered()
    from backend.evaluation.dataset import load_dataset, load_dataset_file

    if dataset_file:
        # 自定义评测集文件 → 用 rag runner 跑（dataset_file 覆盖 module）
        cases = load_dataset_file(dataset_file, default_module="rag")
        if smoke:
            cases = cases[:5]
        results = run_module("rag", cases, live=live, judge=judge)
        return EvalReport(
            module="rag",
            mode="live" if live else "offline",
            smoke=smoke,
            summaries=[_build_summary(results, "rag")],
            results=list(results),
            total_score=None,
        )

    module_kinds: list[ModuleKind] = (
        ["planner", "rag", "sql", "e2e"] if module == "all" else [module]  # type: ignore
    )

    all_results: list[EvalResult] = []
    summaries: list[ModuleSummary] = []

    for m in module_kinds:
        cases = load_dataset(m)
        if smoke:
            cases = cases[:5]

        results = run_module(m, cases, live=live, judge=judge)
        all_results.extend(results)
        summaries.append(_build_summary(results, m))

    # 综合评分（仅全量 + live 模式计算）
    total_score = None
    if module == "all" and live:
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

    return EvalReport(
        module=module,
        mode="live" if live else "offline",
        smoke=smoke,
        summaries=summaries,
        results=all_results,
        total_score=total_score,
    )
