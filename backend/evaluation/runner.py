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
    )
    return EvaluationService().evaluate(config)
