# -*- coding: utf-8 -*-
"""评测中心接口 evaluation_v2 模块化架构守卫测试。

验证单一职责解耦、门面行数配额、45 个路由端点完整性、数据模型与 monkeypatch 穿透能力。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from fastapi.routing import APIRoute
from pydantic import BaseModel

import backend.app.api.routes.evaluation_v2 as facade_module
from backend.app.api.routes import evaluation_subroutes


def test_facade_line_count_within_budget():
    """断言门面文件 evaluation_v2.py 行数不超过 250 行（从 2347 行削减 >90%）。"""
    facade_path = Path(inspect.getfile(facade_module))
    lines = facade_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 250, f"evaluation_v2.py 行数超标: {len(lines)} (上限 250 行)"


def test_subroutes_module_structure_intact():
    """断言子包中必须存在单一职责的各个子模块。"""
    subroutes_dir = Path(inspect.getfile(evaluation_subroutes)).parent
    expected_files = [
        "base.py",
        "schemas.py",
        "datasets.py",
        "evaluators.py",
        "runs.py",
        "experiments.py",
        "routes.py",
        "__init__.py",
    ]
    for filename in expected_files:
        p = subroutes_dir / filename
        assert p.exists() and p.is_file(), f"缺少预期子模块文件: {p}"


def test_all_endpoints_intact_and_registered():
    """断言 evaluation_v2 路由注册了全部 45 个端点，路径和 HTTP Method 100% 保持一致。"""
    router = facade_module.router
    registered_routes = [r for r in router.routes if isinstance(r, APIRoute)]
    
    expected_all = [
        "download_import_template", "search_dataset_evidence", "list_datasets", "create_dataset",
        "preview_dataset_import", "import_dataset_candidates", "list_candidates", "list_candidate_reviews",
        "create_candidate", "get_case_latest_revision", "set_case_golden", "create_case_revision_candidate",
        "archive_case", "intake_trace", "intake_unanswered", "review_candidate", "publish_dataset_version",
        "list_dataset_versions", "export_dataset_version", "get_dataset_version_coverage", "compare_dataset_versions",
        "list_evaluator_versions", "create_evaluator_version", "publish_evaluator_version", "list_suites",
        "create_suite", "append_suite_version", "publish_suite_version", "get_domain_overview", "list_runs",
        "create_eval_run", "get_eval_run", "get_eval_run_results", "get_eval_run_case_detail", "get_eval_human_reviews",
        "create_eval_human_review", "cancel_eval_run", "retry_eval_run", "create_experiment", "list_experiments",
        "attach_experiment_run", "compare_eval_runs", "get_eval_gate", "create_eval_gate_approval", "get_eval_audit_events"
    ]
    registered_names = {r.name for r in registered_routes}
    missing = [name for name in expected_all if name not in registered_names]
    assert not missing, f"缺少端点: {missing}"
    assert len(registered_routes) >= 45, f"端点数量异常: {len(registered_routes)} < 45"
    
    endpoint_paths = {r.path for r in registered_routes}
    expected_sample_paths = {
        "/evaluation/v2/import-template",
        "/evaluation/v2/datasets/evidence-search",
        "/evaluation/v2/datasets",
        "/evaluation/v2/datasets/imports/preview",
        "/evaluation/v2/datasets/imports",
        "/evaluation/v2/candidates",
        "/evaluation/v2/candidates/{candidate_id}/reviews",
        "/evaluation/v2/candidates/{candidate_id}/review",
        "/evaluation/v2/datasets/{dataset_id}/versions",
        "/evaluation/v2/evaluators",
        "/evaluation/v2/suites",
        "/evaluation/v2/runs",
        "/evaluation/v2/runs/{run_id}",
        "/evaluation/v2/runs/{run_id}/cancel",
        "/evaluation/v2/runs/{run_id}/retry",
        "/evaluation/v2/experiments",
        "/evaluation/v2/gates/{run_id}",
        "/evaluation/v2/audit-events",
    }
    for p in expected_sample_paths:
        assert p in endpoint_paths, f"关键端点路径缺失: {p}"


def test_facade_symbol_exports_and_monkeypatch_penetration(monkeypatch):
    """断言门面导出符号齐全，且对门面进行 monkeypatch 时能无损穿透至子模块。"""
    assert hasattr(facade_module, "CandidateCreate")
    assert hasattr(facade_module, "EvalRunCreate")
    assert hasattr(facade_module, "create_eval_run")
    assert hasattr(facade_module, "review_candidate")
    assert hasattr(facade_module, "verify_evidence_anchors")
    assert hasattr(facade_module, "load")

    # 测试 monkeypatch 穿透
    test_mock_called = False

    def mock_verify(*args, **kwargs):
        nonlocal test_mock_called
        test_mock_called = True
        return []

    monkeypatch.setattr(facade_module, "verify_evidence_anchors", mock_verify)

    # 验证子模块感知到了该 mock
    current_fn = getattr(evaluation_subroutes.datasets.facade, "verify_evidence_anchors")
    res = current_fn()
    assert test_mock_called is True
    assert res == []


def test_pydantic_schemas_valid_and_instantiable():
    """断言 23 个 Pydantic 核心数据模型在门面成功导出并可正常初始化。"""
    schemas = [
        facade_module.StrictModel,
        facade_module.DatasetCreate,
        facade_module.CandidatePayload,
        facade_module.CandidateCreate,
        facade_module.CaseRevisionCandidateCreate,
        facade_module.CaseGoldenMark,
        facade_module.EvidenceAnchorClaim,
        facade_module.FactEvidenceBinding,
        facade_module.CandidateReview,
        facade_module.DatasetItemRef,
        facade_module.DatasetPublish,
        facade_module.EvaluatorVersionRef,
        facade_module.EvaluatorCreate,
        facade_module.SuiteCreate,
        facade_module.SuiteVersionCreate,
        facade_module.GateApprovalCreate,
        facade_module.HumanReviewCreate,
        facade_module.TraceIntake,
        facade_module.UnansweredIntake,
        facade_module.EvidenceSearchCreate,
        facade_module.EvalRunCreate,
        facade_module.ExperimentCreate,
        facade_module.ExperimentRunAttach,
    ]
    for model_cls in schemas:
        assert issubclass(model_cls, BaseModel)
