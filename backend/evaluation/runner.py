"""评估执行引擎 — run_all 薄包装 + Planner 离线评估。

run_all() 委托给 EvaluationService.evaluate()。
evaluate_planner_offline() 是纯函数，供 planners runner 使用。
"""

from typing import Any

from backend.evaluation.metrics import jaccard_similarity
from backend.evaluation.models import EvalResult


def evaluate_planner_offline(
    case_id: str,
    expected: dict[str, Any],
    actual_capabilities: list[str],
    actual_params: dict[str, dict] | None = None,
) -> EvalResult:
    """离线评估 Planner 输出 — 纯函数，不依赖项目模块。

    评估维度：
    - jaccard: 期望能力与实际能力的 Jaccard 相似度
    - redundancy: 不应出现的能力实际出现的比例
    - structure_ok: edges 中的能力是否都出现在实际能力中
    - param_match: 关键参数断言（expected.params，可缺省）

    expected.params 结构（按 capability 声明关键参数，不做全量对比）：
        {"sql.query": {"question": {"contains": "部门"}},
         "email.send": {"to": {"contains": "@"}}}
    值为标量时按相等断言；{"contains": s} 按子串断言。
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

    # ── 关键参数断言：只覆盖声明了的 (capability, param)。写操作误填代价高
    # （收件人/动作类型），给 Planner 参数填充一个可回归的度量 ──
    param_problems: list[str] = []
    total_checks = 0
    failed_checks = 0
    for cap, param_spec in (expected.get("params") or {}).items():
        params = (actual_params or {}).get(cap)
        if params is None:
            failed_checks += 1
            total_checks += 1
            param_problems.append(f"capability {cap} 未被规划，参数未校验")
            continue
        for name, want in param_spec.items():
            total_checks += 1
            got = params.get(name)
            if isinstance(want, dict) and "contains" in want:
                ok = isinstance(got, str) and want["contains"] in got
            else:
                ok = got == want
            if not ok:
                failed_checks += 1
                param_problems.append(
                    f"{cap}.{name} 期望 {want!r}，实际 {got!r}")

    param_match = (
        round((total_checks - failed_checks) / total_checks, 4)
        if total_checks else None
    )

    passed = (
        jaccard >= 0.5
        and redundancy <= 0.25
        and structure_ok
        and (param_match is None or param_match >= 1.0)
    )

    actual: dict[str, Any] = {"capabilities": actual_capabilities}
    if actual_params:
        actual["params"] = actual_params

    return EvalResult(
        case_id=case_id,
        module="planner",
        status="pass" if passed else "fail",
        expected=expected,
        actual=actual,
        metrics={
            "jaccard": round(jaccard, 4),
            "redundancy": round(redundancy, 4),
            "structure_ok": 1.0 if structure_ok else 0.0,
            "param_match": param_match,
        },
        error_msg="; ".join(param_problems) or None,
    )


def run_all(
    module: str = "all",
    live: bool = False,
    smoke: bool = False,
    judge: bool = False,
    dataset_file: str | None = None,
    tier: str = "all",
    ragas: bool = False,
    no_ragas: bool = False,
    ragas_level: str = "standard",
    selection: str | None = None,
    semantic_thresholds: dict[str, float] | None = None,
    regression: bool = False,
    promote_baseline: bool = False,
    workers: int = 1,
    ragas_workers: int = 4,
    resume: bool = True,
    multiquery: bool = False,
    full_trace: bool = False,
) -> Any:
    """主入口 — 薄包装器，委托给 EvaluationService.evaluate()。

    签名不变，CLI / API / CI 调用方无需修改。
    """
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.service import EvaluationService

    config = EvalConfig(
        module=module,
        live=live,
        smoke=smoke,
        judge=judge,
        dataset=dataset_file,
        tier=tier,
        ragas=ragas,
        no_ragas=no_ragas,
        ragas_level=ragas_level,
        selection=selection,
        semantic_thresholds=semantic_thresholds,
        regression=regression,
        promote_baseline=promote_baseline,
        workers=workers,
        ragas_workers=ragas_workers,
        resume=resume,
        multiquery=multiquery,
        full_trace=full_trace,
    )
    return EvaluationService().evaluate(config)
