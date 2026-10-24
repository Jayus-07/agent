"""受控执行器当前明确支持的评测模块。"""

from typing import Any

SUPPORTED_RUNNER_MODULES = frozenset({
    "rag", "sql", "cs", "e2e", "travel", "travel-provider", "travel-commerce",
})
CONTRACT_ONLY_RUNNER_MODULES = frozenset({"sql", "cs", "e2e"})
QUALITY_RUNNER_MODULES = frozenset(SUPPORTED_RUNNER_MODULES - CONTRACT_ONLY_RUNNER_MODULES - {"rag"})
SUPPORTED_RUNNER_BINDINGS = {
    "answer_quality": "deterministic",
    "evidence_quality": "evidence",
    "human_review": "human",
}
MACHINE_RUNNER_BINDINGS = frozenset({"answer_quality", "evidence_quality"})


class UnsupportedRunnerCapabilityError(ValueError):
    """当前 Runner 未适配的公开能力请求。"""


def require_supported_runner_module(module: str) -> str:
    """阻止创建无法由当前 V2 Runner 执行的方案。"""
    normalized = str(module).strip()
    if normalized not in SUPPORTED_RUNNER_MODULES:
        raise ValueError(
            f"当前 V2 Runner 尚未适配 {normalized or '空'} 模块；"
            "只有已接入并完成契约验证的模块才能创建可运行方案"
        )
    return normalized


def require_dataset_domain_match(module: str, dataset_domain: str) -> None:
    """防止把其他业务域的 Golden 误接入当前 Runner。"""
    normalized_module = str(module).strip()
    normalized_domain = str(dataset_domain).strip()
    if normalized_module != normalized_domain:
        raise ValueError(
            f"评测方案模块 {normalized_module or '空'} 与 Golden 业务域 "
            f"{normalized_domain or '空'} 不一致"
        )


def require_supported_runner_evaluators(
    evaluator_refs: list[dict[str, Any]],
    evaluator_kinds: dict[str, str],
    *,
    module: str = "rag",
) -> None:
    """拒绝把当前 Runner 无法执行的评分器绑定到可运行 Suite。"""
    supported_bindings = SUPPORTED_RUNNER_BINDINGS
    machine_bindings = MACHINE_RUNNER_BINDINGS
    if module != "rag":
        # 旧领域 Runner 自带断言，不会消费 RAG 证据评分器。
        supported_bindings = {"answer_quality": "deterministic", "human_review": "human"}
        machine_bindings = frozenset({"answer_quality"})
    if not evaluator_refs:
        raise ValueError("方案至少需要一个可运行的机器评分绑定")

    has_machine_binding = False
    for item in evaluator_refs:
        evaluator_id = str(item.get("evaluator_version_id", ""))
        binding = str(item.get("binding", ""))
        expected_kind = supported_bindings.get(binding)
        if expected_kind is None:
            raise UnsupportedRunnerCapabilityError(
                "当前 V2 Runner 不支持该评分绑定；请使用已适配的评分绑定。"
            )
        actual_kind = evaluator_kinds.get(evaluator_id)
        if actual_kind != expected_kind:
            raise UnsupportedRunnerCapabilityError(
                f"{binding} 绑定当前要求 {expected_kind} 评分器；所选版本未适配。"
            )
        has_machine_binding = has_machine_binding or binding in machine_bindings

    if not has_machine_binding:
        raise UnsupportedRunnerCapabilityError(
            "评测方案至少需要一个已适配的机器评分绑定；人工复核只能作为补充。"
        )
