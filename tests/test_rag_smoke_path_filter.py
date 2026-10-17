"""RAG Smoke 路径过滤及真实 Provider 预检契约。"""
from __future__ import annotations

import importlib.util
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


def test_unrelated_migrations_do_not_trigger_main_paid_smoke():
    paths = [
        "backend/sql/migrations/006_customer_service.sql",
        "backend/sql/migrations/052_travel_booking.sql",
        "backend/sql/migrations/058_cs_case.sql",
        "backend/sql/migrations/098_cs_refund_order_unique.sql",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


def test_unclassified_migration_does_not_match_fuzzy_keywords():
    assert _filter_import(
        ["backend/sql/migrations/100_new_unknown_table.sql"]
    ) is False
    assert _filter_subprocess(
        ["backend/sql/migrations/100_new_unknown_table.sql"]
    ) is False
    # 新迁移至少执行 PR 免费契约门禁；是否需要真实评测由显式分类决定。
    assert _filter_import(
        ["backend/sql/migrations/100_new_unknown_table.sql"], event="pull_request"
    ) is True
    assert _filter_subprocess(
        ["backend/sql/migrations/100_new_unknown_table.sql"], event="pull_request"
    ) is True


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
        ".github/scripts/rag_smoke_path_filter.py",
        "backend/scripts/seed_prompt_eval_db.py",
        "scripts/seed_eval_model_bindings.py",
        "tests/evaluation/test_rag_ci_workflow_contracts.py",
        "tests/test_rag_smoke_path_filter.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False
    assert _filter_import(paths, event="pull_request") is True
    assert _filter_subprocess(paths, event="pull_request") is True


def test_non_rag_docs_and_backend_changes_skip_smoke():
    paths = [
        "docs/operations/ci-evaluation-environment.md",
        "README.md",
        "frontend/src/components/Foo.tsx",
        "backend/tools/registry.py",
        "backend/domains/travel/graph.py",
    ]
    assert _filter_import(paths) is False
    assert _filter_subprocess(paths) is False


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
        provider_probe = next(
            i for i, step in enumerate(steps)
            if step.get("name") == "Verify bindings and probe real API"
        )
        assert price_check < provider_probe
