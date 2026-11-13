"""RAG Smoke 路径过滤及真实 Provider 预检契约。"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import yaml

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "rag_smoke_path_filter.py"
_spec = importlib.util.spec_from_file_location("rag_smoke_path_filter", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _filter_import(paths: list[str], event: str = "push") -> bool:
    return any(
        _mod.is_relevant(path)
        or (event == "pull_request" and _mod.is_pr_static_only(path))
        for path in paths
    )


def _filter_subprocess(paths: list[str], event: str = "push") -> bool:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--event", event],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    line = result.stdout.strip().splitlines()[-1]
    assert line.startswith("relevant="), result.stdout
    return line.split("=", 1)[1] == "true"


def test_rag_and_billing_migrations_trigger_smoke():
    paths = [
        "backend/sql/migrations/013_obs_analytics_pg.sql",
        "backend/sql/migrations/014_rag_stores_pg.sql",
        "backend/sql/migrations/053_model_price_immutable_trigger.sql",
        "backend/sql/migrations/079_llm_usage_billing_v2.sql",
        "backend/sql/migrations/097_llm_model_health_probe_source.sql",
        "backend/sql/migrations/066_budget_cny_identity_needs_review.sql",
    ]
    assert _filter_import(paths) is True
    assert _filter_subprocess(paths) is True


def test_rag_runtime_paths_trigger_smoke():
    paths = [
        "backend/rag/pipeline.py",
        "backend/evaluation/runners/rag.py",
        "backend/core/tool_runtime/policy.py",
        "backend/app/api/routes/rag_upload.py",
        "backend/infra/llm/credentials.py",
        "mcp_servers/servers/rag.py",
        "backend/scripts/ingest_eval_fixtures.py",
        "backend/observability/llm_usage_store.py",
        "backend/observability/llm_usage_store_pg.py",
        "backend/observability/usage_parse.py",
        "backend/tasks/budget_reconciliation_tasks.py",
        "backend/services/model_config.py",
    ]
    for path in paths:
        assert _filter_import([path]) is True, path
        assert _filter_subprocess([path]) is True, path


def test_legacy_evaluation_history_uses_free_pr_preflight_only():
    paths = [
        "backend/app/api/routes/evaluation.py",
        "backend/tests/api/test_evaluation_runs_list.py",
    ]
    assert _filter_import(paths, "pull_request") is True
    assert _filter_subprocess(paths, "pull_request") is True
    assert _filter_import(paths, "push") is False
    assert _filter_subprocess(paths, "push") is False


def test_memory_golden_contract_changes_use_free_pr_preflight_only():
    paths = [
        "backend/scripts/eval_memory_golden.py",
        "backend/evaluation/datasets/memory_golden.manifest.json",
        "backend/tests/memory/test_golden_dataset_contract.py",
    ]
    assert _filter_import(paths, "pull_request") is True
    assert _filter_subprocess(paths, "pull_request") is True
    assert _filter_import(paths, "push") is False
    assert _filter_subprocess(paths, "push") is False


def test_travel_quality_contract_changes_use_free_pr_preflight_only():
    paths = [
        "backend/evaluation/runners/travel.py",
        "backend/evaluation/datasets/travel/cases.jsonl",
        "backend/evaluation/datasets/travel/manifest.json",
        "backend/tests/travel/test_travel_runner_contract.py",
    ]
    assert _filter_import(paths, "pull_request") is True
    assert _filter_subprocess(paths, "pull_request") is True
    assert _filter_import(paths, "push") is False
    assert _filter_subprocess(paths, "push") is False


def test_unrelated_migrations_do_not_trigger_main_paid_smoke():
    paths = [
        "backend/sql/migrations/006_customer_service.sql",
        "backend/sql/migrations/052_travel_booking.sql",
        "backend/sql/migrations/058_cs_case.sql",
        "backend/sql/migrations/098_cs_refund_order_unique.sql",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def _run_filter(paths: list[str], event: str):
    # 子进程中文报错在 Windows 默认 GBK 控制台下会破坏 UTF-8 解码，显式固定。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--event", event],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_unclassified_migration_blocks_pr_free_preflight_and_gates_main():
    """未分类迁移：PR 免费预检失败（可操作报错），main push 不被静默跳过。"""
    path = "backend/sql/migrations/102_new_unknown_table.sql"
    assert _mod.classify_migration(path) == "unknown"

    pull = _run_filter([path], "pull_request")
    assert pull.returncode == 2
    assert "未分类迁移" in pull.stderr
    assert path in pull.stderr

    push = _run_filter([path], "push")
    assert push.returncode == 0
    assert push.stdout.strip().endswith("relevant=true")


def test_every_repository_migration_is_explicitly_classified():
    """完整性检查：仓库内每个迁移都必须显式归类，禁止隐式漏检。"""
    root = Path(__file__).resolve().parents[1]
    migrations = sorted(
        p.name for p in (root / "backend" / "sql" / "migrations").glob("*.sql")
    )
    assert migrations
    unknown = [
        name for name in migrations
        if _mod.classify_migration(f"backend/sql/migrations/{name}") == "unknown"
    ]
    assert unknown == [], unknown


def test_migration_classification_categories():
    assert _mod.classify_migration(
        "backend/sql/migrations/079_llm_usage_billing_v2.sql"
    ) == "paid"
    assert _mod.classify_migration(
        "backend/sql/migrations/024_rag_eval_fixture_set.sql"
    ) == "paid"
    assert _mod.classify_migration(
        "backend/sql/migrations/098_cs_refund_order_unique.sql"
    ) == "non_ai"
    assert _mod.classify_migration(
        "backend/sql/migrations/101_travel_v2_trip_conversations.sql"
    ) == "non_ai"
    assert _mod.classify_migration(
        "backend/sql/migrations/099_model_price_source_monitor.sql"
    ) == "non_ai"


def test_model_price_monitor_changes_do_not_trigger_main_paid_smoke():
    # 复现 PR #79 的完整变更路径：通用 LLM 前缀和 model 迁移标记不能
    # 把只读价格巡检误判为需要付费 Provider 评测的 RAG 变更。
    paths = [
        "backend/app/api/routes/model_prices.py",
        "backend/infra/llm/price_page_monitor.py",
        "backend/observability/alerts.py",
        "backend/sql/migrations/099_model_price_source_monitor.sql",
        "backend/tasks/celery_app.py",
        "backend/tasks/model_price_monitor_tasks.py",
        "backend/tasks/queue_router.py",
        "backend/tests/test_model_price_page_monitor.py",
        "backend/tests/test_price_api_contract.py",
        "backend/tests/test_worker_topology.py",
        "docs/governance/llm-budget-billing.md",
        "frontend-admin/src/api/modelPrices.test.ts",
        "frontend-admin/src/api/modelPrices.ts",
        "frontend-admin/src/app/settings/models/page.tsx",
        "frontend-admin/src/components/model-config/ModelPriceMonitorTab.test.tsx",
        "frontend-admin/src/components/model-config/ModelPriceMonitorTab.tsx",
        "scripts/init_db.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def test_real_rag_change_still_triggers_with_price_monitor_change():
    paths = [
        "backend/infra/llm/price_page_monitor.py",
        "backend/rag/pipeline.py",
    ]
    assert _filter_import(paths) is True
    assert _filter_subprocess(paths) is True


def test_setup_and_workflow_only_changes_run_pr_static_but_not_main_paid_smoke():
    paths = [
        "scripts/init_db.py",
        ".github/workflows/rag_smoke.yml",
        ".github/workflows/rag_regression.yml",
        ".github/workflows/publish-images.yml",
        ".github/workflows/deploy-production.yml",
        ".github/scripts/rag_smoke_path_filter.py",
        "scripts/release.sh",
        "backend/scripts/verify_release_gate.py",
        "backend/scripts/validate_production_secrets.py",
        "backend/tests/scripts/test_release_script_identity.py",
        "backend/tests/scripts/test_validate_production_secrets.py",
        "backend/tests/scripts/test_verify_release_gate_readiness.py",
        "backend/tests/scripts/test_verify_release_gate_build_identity.py",
        "部署/prod-4c8g/docker-compose.release.yml",
        "backend/scripts/seed_prompt_eval_db.py",
        "scripts/seed_eval_model_bindings.py",
        "tests/evaluation/test_rag_ci_workflow_contracts.py",
        "tests/test_rag_smoke_path_filter.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_eval_contract_changes_run_free_pr_tests_not_main_paid_smoke():
    paths = [
        "backend/evaluation/datasets/memory_golden.manifest.json",
        "backend/tests/evaluation/test_release_gate_module_aware.py",
        "backend/tests/evaluation/test_report_gate_verdict.py",
        "backend/tests/evaluation/test_cli_compare.py",
        "tests/evaluation/test_cli_strict_gate_exit.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_eval_contract_tests_are_in_free_pr_workflow():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    run_steps = [
        step for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "run" in step
    ]
    for registered in (
        "backend/tests/memory/test_golden_dataset_contract.py",
        "backend/tests/travel/test_travel_runner_contract.py",
        "backend/tests/evaluation/test_report_gate_verdict.py",
        "backend/tests/evaluation/test_cli_compare.py",
        "tests/evaluation/test_cli_strict_gate_exit.py",
        "backend/tests/context_budget/test_auto_compact.py",
        "backend/tests/context_budget/test_stop_c_event_isolation.py",
        "backend/tests/context_budget/test_golden_eval_seed.py",
        "backend/tests/api/test_travel_context_budget_events.py",
        "backend/tests/api/test_auth_local.py",
        "backend/tests/test_model_override_validation.py",
        "backend/tests/orchestration/graph/test_clarify_flow.py",
        "backend/tests/orchestration/router/test_product_scope.py",
    ):
        assert any(registered in step["run"] for step in run_steps), (
            f"rag_smoke 定向清单缺少 {registered}（PR 静态门禁空转）"
        )


def test_context_budget_and_chat_surface_changes_run_free_pr_preflight():
    """新上下文链路走免费回归，聊天/认证路由不误触付费 RAG 评测。"""
    paths = [
        "backend/context_budget/manager.py",
        "backend/app/api/routes/chat.py",
        "backend/app/api/routes/travel.py",
        "backend/app/api/routes/auth_local.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_non_rag_docs_and_backend_changes_skip_smoke():
    """main push 语义：以下路径不触发付费评测面（backend/tools/ 于 PR 上
    走免费预检，见 test_tool_memory_funnel_changes_run_pr_static）。"""
    paths = [
        "docs/operations/ci-evaluation-environment.md",
        "README.md",
        "frontend/src/components/Foo.tsx",
        "backend/tools/registry.py",
        "backend/domains/travel/graph.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def test_tool_memory_funnel_changes_run_pr_static_but_not_main_paid_smoke():
    """#437 评审缺口收口：tools/memory/selection_funnel 源码目录的变更
    在 PR 上触发免费定向预检（清单已收录三个目录的测试），main push
    不触发付费评测面——与治理面（admin_*）同款语义。"""
    paths = [
        "backend/tools/map/route.py",
        "backend/tools/memory.py",
        "backend/memory/service.py",
        "backend/selection_funnel/reporter.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_tool_memory_funnel_checklist_not_idle():
    """清单守卫（防「过滤器说触发、清单没跑用例」空转）：三个目录前缀
    触发 PR 免费预检的前提，是定向清单真的包含各自目录的测试。"""
    root = Path(__file__).resolve().parents[1]
    smoke = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    run_steps = [
        step for job in smoke["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "run" in step
    ]
    for registered in (
        "backend/tests/tools/test_map_navigation_key.py",
        "backend/tests/memory/test_memory_store_tool_keying.py",
        "backend/tests/selection_funnel/test_pool_sources.py",
    ):
        assert any(registered in step["run"] for step in run_steps), (
            f"rag_smoke 定向清单缺少 {registered}（触发面与清单脱节）"
        )


def test_admin_governance_changes_run_pr_static_but_not_main_paid_smoke():
    """管理端工具治理面（admin_mcp/admin_tools/admin_llm）：PR 免费预检触发，
    main push 不触发真实 Provider 评测——载荷契约测试进 CI 门禁的路径入口
    （PR #414 评审发现：TestToolListPayload 此前不被任何 workflow 执行）。"""
    paths = [
        "backend/app/api/routes/admin_mcp.py",
        "backend/app/api/routes/admin_tools.py",
        "backend/shared/admin_llm.py",
        "backend/tests/api/test_admin_mcp_api.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_tool_list_payload_contract_in_smoke_checklist():
    """过滤器把治理面归类为 PR 免费预检后，定向清单必须真的包含
    TestToolListPayload——防止「过滤器说触发、清单没跑用例」的门禁空转。"""
    root = Path(__file__).resolve().parents[1]
    smoke = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    run_steps = [
        step for job in smoke["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "run" in step
    ]
    assert any(
        "backend/tests/api/test_admin_mcp_api.py::TestToolListPayload" in step["run"]
        for step in run_steps
    ), "rag_smoke 定向清单缺少 TestToolListPayload（载荷契约门禁空转）"


def test_empty_diff_skips_smoke():
    assert _filter_import([]) is False
    assert _filter_subprocess([]) is False


def test_approved_price_check_precedes_provider_probe():
    root = Path(__file__).resolve().parents[1]
    smoke = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    detect_steps = smoke["jobs"]["detect-changes"]["steps"]
    filter_step = next(step for step in detect_steps if step.get("id") == "filter")
    assert '--event "$EVENT_NAME"' in filter_step["run"]

    for workflow_name in ("rag_smoke.yml", "rag_regression.yml"):
        workflow = yaml.safe_load(
            (root / ".github/workflows" / workflow_name).read_text(
                encoding="utf-8"
            )
        )
        job_name = "rag-smoke" if workflow_name == "rag_smoke.yml" else "rag-regression"
        job = workflow["jobs"][job_name]
        assert "PROMPT_EVAL_APPROVED_PRICES_JSON" not in job["env"]
        steps = job["steps"]
        seed = next(step for step in steps if step.get("name") == "Seed DB API model bindings")
        assert seed["run"].endswith("--require-approved-prices")
        assert seed["env"]["PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL"] == (
            "$" + "{{ secrets.PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL }}"
        )
        assert "PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL" not in job["env"]
        price_check = next(
            i for i, step in enumerate(steps)
            if step.get("name") == "Verify approved prices before any paid API call"
        )
        if workflow_name == "rag_smoke.yml":
            # Smoke 的真实评测本身受账本和预算门禁约束，不额外发起未计入
            # 评测账本的独立 Provider Probe。
            assert not any(
                step.get("name") == "Verify bindings and probe real API"
                for step in steps
            )
        else:
            provider_probe = next(
                i for i, step in enumerate(steps)
                if step.get("name") == "Verify bindings and probe real API"
            )
            assert price_check < provider_probe
