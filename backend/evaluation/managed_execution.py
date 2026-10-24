"""业务评测的执行服务；HTTP、Worker 与发布门禁复用同一实现。"""

from __future__ import annotations

from backend.evaluation.dataset import imports as assets


def validate_kb(kb_id: str) -> dict:
    from backend.rag.knowledge_base_store import get_knowledge_base

    item = get_knowledge_base(kb_id, include_inactive=False)
    if not item or item.get("alias_for") or item.get("deprecated"):
        raise ValueError("请选择启用的正式知识库，兼容别名不可评测")
    return item


def execute_binding(
    binding_id: str,
    actor: str,
    *,
    run_id=None,
    boundary=None,
    prompt_versions=None,
    refresh_registry_before_run: bool = True,
) -> dict:
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.service import EvaluationService
    from backend.evaluation.storage import persist_report
    from backend.infra.async_utils import run_on_worker_loop
    from backend.infra.llm.registry_store import refresh_registry

    if refresh_registry_before_run:
        run_on_worker_loop(refresh_registry())

    from backend.evaluation.managed_tasks import case_boundary

    token = case_boundary.set(boundary)
    try:
        report = EvaluationService().evaluate(
            EvalConfig(
                module="rag",
                dataset=f"managed:{binding_id}",
                live=False,
                no_ragas=True,
                triggered_by=actor,
                run_id=run_id,
                resume=True,
                prompt_versions=prompt_versions or {},
            )
        )
    finally:
        case_boundary.reset(token)
    if boundary:
        boundary()
    report.metadata["triggered_by"] = actor
    report.metadata["trigger"] = "dataset_binding"
    from backend.evaluation.release_gate import compute_module_release_gate

    gate = compute_module_release_gate(report)
    report.metadata["release_gate"] = gate
    location = persist_report(report)
    result = {
        "run_id": location.name,
        "validity": report.metadata["run_validity"]["validity"],
        "total": len(report.results),
        "gate": gate["verdict"],
    }
    assets.write_once(
        "executions",
        assets.digest({"binding_id": binding_id, "run_id": location.name})[:24],
        {
            **result,
            "binding_id": binding_id,
            "finished_at": assets.now(),
            "actor": actor,
        },
    )
    return result
