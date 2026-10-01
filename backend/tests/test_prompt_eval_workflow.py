"""Prompt release evaluation workflow 的最小契约测试。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/prompt_eval.yml")


def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML 1.1 会把未加引号的 on 解析为 True，兼容两种解析结果。
    triggers = data.get("on") or data.get(True)
    data["on"] = triggers
    return data


def test_prompt_eval_workflow_is_manual_or_repository_dispatch_only():
    workflow = _workflow()
    triggers = workflow["on"]

    assert "workflow_dispatch" in triggers
    assert "repository_dispatch" in triggers
    assert set(triggers) == {"workflow_dispatch", "repository_dispatch"}
    assert "pull_request" not in triggers
    assert "production" not in json.dumps(workflow["jobs"], ensure_ascii=False)


def test_prompt_eval_workflow_accepts_release_contract_inputs():
    workflow = _workflow()
    dispatch = workflow["on"]["workflow_dispatch"]
    repository = workflow["on"]["repository_dispatch"]

    assert set(dispatch["inputs"]) >= {
        "release_id", "prompt_key", "version", "suite", "external_run_id",
    }
    assert "callback_url" not in dispatch["inputs"]
    assert repository["types"] == ["prompt-eval"]
    assert "CALLBACK_URL" not in workflow["jobs"]["prompt-eval"]["env"]
    assert "run-name" in workflow
    assert "external_run_id" in workflow["run-name"]


def test_prompt_eval_routes_memory_backed_stores_to_isolated_database():
    workflow = _workflow()
    env = workflow["jobs"]["prompt-eval"]["env"]

    assert env["MEMORY_PGDATABASE"] == "agent_memory_test"
    assert env["VECTOR_PGDATABASE"] == "agent_memory_test"
    assert env["RAG_STORES_PGDATABASE"] == "agent_memory_test"
    assert env["DOC_REGISTRY_PGDATABASE"] == "agent_memory_test"
    assert env["OBS_DB_PGDATABASE"] == "agent_memory_test"
    assert env["WORKFLOW_DB_PGDATABASE"] == "agent_memory_test"


def test_prompt_eval_imports_baseline_fixtures_before_evaluation():
    workflow = _workflow()
    steps = workflow["jobs"]["prompt-eval"]["steps"]
    names = [step.get("name") for step in steps]
    import_step = next(
        step for step in steps if step.get("name") == "Import baseline evaluation fixtures"
    )

    assert "Import baseline evaluation fixtures" in names
    assert import_step["run"] == "python -m backend.evaluation.import_fixture baseline"
    assert names.index("Import baseline evaluation fixtures") < names.index(
        "Run selected evaluation"
    )
