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
        "release_id", "prompt_key", "version", "suite", "callback_url",
    }
    assert repository["types"] == ["prompt-eval"]
    assert "CALLBACK_URL" in workflow["jobs"]["prompt-eval"]["env"]
