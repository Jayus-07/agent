"""评估执行引擎 — run_all 薄包装 + Planner 离线评估。

run_all() 委托给 EvaluationService.evaluate()。
evaluate_planner_offline() 是纯函数，供 planners runner 使用。
"""

from collections import Counter
from typing import Any

from backend.evaluation.metrics import jaccard_similarity
from backend.evaluation.models import EvalResult


def evaluate_planner_offline(
    case_id: str,
    expected: dict[str, Any],
    actual_capabilities: list[str] | None = None,
    actual_params: dict[str, dict] | None = None,
    actual_plan: dict[str, Any] | None = None,
) -> EvalResult:
    """离线评估 Planner 输出 — 纯函数，不依赖项目模块。

    评估维度：
    - jaccard: 期望能力与实际能力的 Jaccard 相似度
    - redundancy: 不应出现的能力实际出现的比例
    - structure_ok: Plan schema、预期依赖边和步骤数量是否满足契约
    - param_match: 关键参数断言（expected.params，可缺省）

    Live Planner Runner 传入完整 actual_plan，按能力多重集和依赖边精确评分。
    旧调用方可继续传 actual_capabilities，沿用能力覆盖率兼容口径。

    expected.params 结构（按 capability 声明关键参数，不做全量对比）：
        {"sql.query": {"question": {"contains": "部门"}},
         "email.send": {"to": {"contains": "@"}}}
    值为标量时按相等断言；{"contains": s} 按子串断言。
    """
    expected_capability_list = expected.get("capabilities", [])
    expected_caps = set(expected_capability_list)
    should_not = set(expected.get("should_not_contain", []))
    actual_plan_errors: list[str] = []
    actual_edge_data: list[dict[str, str]] = []
    node_count: int | None = None
    edge_match: bool | None = None
    step_count_ok = True
    if actual_plan is not None:
        from backend.agents.planner.plan_utils import validate_plan_contract

        actual_plan_errors = validate_plan_contract(actual_plan)
        raw_nodes = actual_plan.get("nodes", [])
        if isinstance(raw_nodes, dict):
            nodes = list(raw_nodes.values())
        elif isinstance(raw_nodes, list):
            nodes = raw_nodes
        else:
            nodes = []
        actual_capabilities = [
            node.get("capability", "")
            for node in nodes
            if isinstance(node, dict) and node.get("capability")
        ]
        actual_params = {
            node.get("capability"): node.get("params") or {}
            for node in nodes
            if isinstance(node, dict) and node.get("capability")
        }
        node_count = len(nodes)
        minimum = expected.get("min_steps")
        maximum = expected.get("max_steps")
        step_count_ok = (
            (minimum is None or node_count >= minimum)
            and (maximum is None or node_count <= maximum)
        )
        if "edges" in expected:
            node_by_id = {
                str(node.get("step_id")): node
                for node in nodes
                if isinstance(node, dict) and node.get("step_id") is not None
            }
            actual_edges: Counter[tuple[str, str]] = Counter()
            raw_edges = actual_plan.get("edges", {})
            if isinstance(raw_edges, dict):
                for target, dependencies in raw_edges.items():
                    if not isinstance(dependencies, list):
                        continue
                    target_node = node_by_id.get(str(target))
                    if not target_node:
                        continue
                    for dependency in dependencies:
                        dependency_node = node_by_id.get(str(dependency))
                        if dependency_node:
                            source_capability = str(
                                dependency_node.get("capability", ""))
                            target_capability = str(
                                target_node.get("capability", ""))
                            actual_edges[(source_capability, target_capability)] += 1
                            actual_edge_data.append({
                                "from": source_capability,
                                "to": target_capability,
                            })
            expected_edges: Counter[tuple[str, str]] = Counter()
            raw_expected_edges = expected.get("edges")
            if isinstance(raw_expected_edges, list):
                for edge in raw_expected_edges:
                    if isinstance(edge, dict):
                        expected_edges[(
                            str(edge.get("from", "")),
                            str(edge.get("to", "")),
                        )] += 1
            edge_match = actual_edges == expected_edges

    actual_capabilities = actual_capabilities or []
    actual_set = set(actual_capabilities)

    jaccard = jaccard_similarity(actual_set, expected_caps)

    redundancy_hits = should_not & actual_set
    redundancy = len(redundancy_hits) / len(should_not) if should_not else 0.0

    structure_ok = not actual_plan_errors and step_count_ok
    if actual_plan is not None and edge_match is not None:
        structure_ok = structure_ok and edge_match
    elif actual_plan is None and "edges" in expected:
        edge_caps = set()
        for edge in expected["edges"]:
            edge_caps.add(edge["from"])
            edge_caps.add(edge["to"])
        structure_ok = structure_ok and edge_caps.issubset(actual_set)

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

    exact_capabilities = Counter(actual_capabilities) == Counter(expected_capability_list)
    passed = (
        (exact_capabilities if actual_plan is not None else jaccard >= 0.5)
        and redundancy <= 0.25
        and structure_ok
        and (param_match is None or param_match >= 1.0)
    )

    problems = list(actual_plan_errors)
    if actual_plan is not None and not exact_capabilities:
        problems.append("实际 capability 集合与预期不一致")
    if actual_plan is not None and not step_count_ok:
        problems.append("实际步骤数量不在预期范围内")
    if actual_plan is not None and edge_match is False:
        problems.append("实际依赖边与预期不一致")
    problems.extend(param_problems)

    actual: dict[str, Any] = {"capabilities": actual_capabilities}
    if actual_params:
        actual["params"] = actual_params
    if actual_plan is not None:
        actual["node_count"] = node_count
        actual["edges"] = actual_edge_data
        actual["edge_match"] = edge_match
        actual["plan_contract_errors"] = actual_plan_errors

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
            "capability_match": 1.0 if exact_capabilities else 0.0,
            "edge_match": None if edge_match is None else (1.0 if edge_match else 0.0),
            "step_count_ok": 1.0 if step_count_ok else 0.0,
            "node_count": node_count,
            "param_match": param_match,
        },
        error_msg="; ".join(problems) or None,
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
    kb_id: str | None = None,
    fixture_set: str | None = None,
    dataset_version: str | None = None,
    run_id: str | None = None,
    semantic_thresholds: dict[str, float] | None = None,
    regression: bool = False,
    promote_baseline: bool = False,
    workers: int = 1,
    ragas_workers: int = 4,
    resume: bool = True,
    multiquery: bool = False,
    full_trace: bool = False,
    force_rerun: bool = False,
    cs_mode: str = 'graph_only',
) -> Any:
    """主入口 — 薄包装器，委托给 EvaluationService.evaluate()。

    新增参数均有默认值，CLI / API / CI 旧调用方无需修改。
    """
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.service import EvaluationService

    config = EvalConfig(
        module=module,
        live=live,
        cs_mode=cs_mode,
        smoke=smoke,
        judge=judge,
        dataset=dataset_file,
        tier=tier,
        ragas=ragas,
        no_ragas=no_ragas,
        ragas_level=ragas_level,
        selection=selection,
        kb_id=kb_id,
        fixture_set=fixture_set,
        dataset_version=dataset_version,
        run_id=run_id,
        semantic_thresholds=semantic_thresholds,
        regression=regression,
        promote_baseline=promote_baseline,
        workers=workers,
        ragas_workers=ragas_workers,
        resume=resume,
        multiquery=multiquery,
        full_trace=full_trace,
        force_rerun=force_rerun,
    )
    return EvaluationService().evaluate(config)
