"""test_repository_modular_architecture.py — 评测仓储物理模块化架构守卫测试。

本测试严格守卫以下架构红线:
1. `backend/evaluation_v2/repository.py` 必须作为瘦门面（Facade），代码行数不得超过 250 行；
2. 门面必须 100% 导出历史全部公共函数、常量和异常类（共 74 个核心符号），防止出现导入断裂；
3. `backend/evaluation_v2/repositories/` 子包中的各个子模块必须具备清晰的单一职责且可独立导入。
"""
import inspect
from pathlib import Path

import pytest


REQUIRED_EXPORTS = {
    # 异常与常量
    "RepositoryConflictError",
    "RepositoryNotFoundError",
    "RepositoryExportLimitError",
    "_TENANT_RE",
    "_VERSION_RE",
    # 基础组件
    "canonical_json",
    "content_hash",
    "normalize_human_rubric",
    "_tenant",
    "_transaction",
    "_record_audit",
    "_dict",
    "_valid_evidence",
    "list_audit_events",
    # 数据集
    "list_datasets",
    "get_dataset_domain",
    "create_dataset",
    "ensure_dataset",
    "list_dataset_versions",
    "get_dataset_version_coverage",
    "load_dataset_version_export",
    "record_dataset_version_export",
    "compare_dataset_versions",
    "list_dataset_version_evidence",
    "list_evaluator_versions",
    "create_evaluator_version",
    "publish_evaluator_version",
    "_validate_evaluator_refs",
    "_normalize_suite_selection",
    "publish_dataset_version",
    "list_suites",
    "create_suite",
    "append_suite_version",
    "publish_suite_version",
    # 候选集与用例
    "_candidate_domain",
    "get_candidate_review_context",
    "list_case_evidence",
    "create_candidate",
    "find_candidate_by_source_origin",
    "get_case_latest_revision",
    "create_case_revision_candidate",
    "archive_case",
    "set_case_golden",
    "latest_golden_map",
    "create_candidates_bulk",
    "count_duplicate_queries",
    "list_candidates",
    "list_candidate_annotations",
    "review_candidate",
    # 评测运行
    "get_run_definition",
    "get_run_by_request",
    "create_run",
    "_validate_run_environment",
    "get_run",
    "list_runs",
    "_normalize_ledger_row",
    "_fetch_ledger_runs",
    "list_runs_unified",
    "get_run_unified",
    "get_domain_overview",
    "set_run_status",
    "append_run_results",
    "list_run_results",
    "get_run_result",
    "list_human_reviews",
    "record_human_review",
    # 门禁决策
    "record_gate_decision",
    "terminate_run_with_gate",
    "complete_run_with_gate",
    "list_gate_decisions",
    "record_gate_approval",
    "list_gate_approvals",
    # 实验对比
    "list_experiments",
    "create_experiment",
    "attach_experiment_run",
    "compare_runs",
}


class TestRepositoryModularArchitectureGuard:
    """评测仓储层物理模块化架构守卫测试套件。"""

    def test_repository_facade_file_size_under_limit(self):
        """守卫: repository.py 作为门面层代码行数必须小于 250 行（治理前 4016 行）。"""
        import backend.evaluation_v2.repository as repo_mod

        repo_file = Path(repo_mod.__file__)
        with open(repo_file, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]

        total_lines = len(repo_file.read_text(encoding="utf-8").splitlines())
        assert total_lines < 250, (
            f"repository.py 当前代码行数为 {total_lines} 行，超过了 250 行架构门限！"
            "请检查是否有业务逻辑泄漏至门面层。"
        )

    def test_repository_exports_all_required_symbols(self):
        """守卫: repository.py 门面必须 100% 完整导出全部 74 个历史契约符号。"""
        import backend.evaluation_v2.repository as repo_mod

        missing_symbols = []
        for symbol in REQUIRED_EXPORTS:
            if not hasattr(repo_mod, symbol):
                missing_symbols.append(symbol)

        assert not missing_symbols, (
            f"repository.py 遗漏了以下必需导出的符号: {missing_symbols}！"
            "为保障系统兼容性，所有公共函数与异常类必须由门面完整导出。"
        )

    def test_repositories_submodules_exist_and_importable(self):
        """守卫: 验证 repositories/ 子包下的 6 个专职子模块独立可导入。"""
        from backend.evaluation_v2.repositories import (
            base,
            datasets,
            candidates,
            runs,
            gates,
            experiments,
        )

        assert inspect.ismodule(base)
        assert inspect.ismodule(datasets)
        assert inspect.ismodule(candidates)
        assert inspect.ismodule(runs)
        assert inspect.ismodule(gates)
        assert inspect.ismodule(experiments)
