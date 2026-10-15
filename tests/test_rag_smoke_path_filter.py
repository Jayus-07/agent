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
        "backend/sql/migrations/014_rag_stores_pg.sql",
        "backend/sql/migrations/053_model_price_immutable_trigger.sql",
        "backend/sql/migrations/079_llm_usage_billing_v2.sql",
        "backend/sql/migrations/097_llm_model_health_probe_source.sql",
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


def test_setup_and_workflow_only_changes_run_pr_static_but_not_main_paid_smoke():
    paths = [
        "scripts/init_db.py",
        ".github/workflows/rag_smoke.yml",
        ".github/workflows/rag_regression.yml",
        ".github/scripts/rag_smoke_path_filter.py",
        "backend/scripts/seed_prompt_eval_db.py",
        "scripts/seed_eval_model_bindings.py",
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
    workflow = yaml.safe_load(
        (root / ".github/workflows/rag_smoke.yml").read_text(encoding="utf-8")
    )
    detect_steps = workflow["jobs"]["detect-changes"]["steps"]
    filter_step = next(step for step in detect_steps if step.get("id") == "filter")
    assert '--event "$EVENT_NAME"' in filter_step["run"]
    job = workflow["jobs"]["rag-smoke"]
    env = job["env"]
    assert env["PROMPT_EVAL_APPROVED_PRICES_JSON"] == (
        "$" + "{{ vars.PROMPT_EVAL_APPROVED_PRICES_JSON }}"
    )
    steps = job["steps"]
    price_check = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Verify approved prices before any paid API call"
    )
    provider_probe = next(
        i for i, step in enumerate(steps)
        if step.get("name") == "Verify bindings and probe real API"
    )
    assert price_check < provider_probe
