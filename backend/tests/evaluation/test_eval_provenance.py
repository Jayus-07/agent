"""评测集、Prompt、模型和 Tool 运行 provenance 测试。"""
from __future__ import annotations

from backend.evaluation.config import EvalConfig
from backend.evaluation.models import EvalReport
from backend.evaluation.provenance import build_eval_provenance


def _report(config: EvalConfig) -> EvalReport:
    return EvalReport(
        module="rag",
        mode="live",
        summaries=[],
        results=[],
        metadata={
            "evaluation_scope": {
                "kb_id": "rag_eval_kb",
                "fixture_set": "baseline",
                "version_id": "rag_eval_kb-baseline-2026-09-18",
                "multiquery": False,
            },
            "selection": config.selection,
            "dataset_version": "5.0.0-unified",
            "prompt_snapshot": {"test.prompt": 1},
        },
        prompt_versions={"test.prompt": 1},
    )


def test_rag_provenance_contains_suite_and_scope():
    config = EvalConfig(module="rag", selection="pr_baseline")

    provenance = build_eval_provenance(config, _report(config))

    assert provenance["suite"] == "pr_baseline"
    assert provenance["kb_id"] == "rag_eval_kb"
    assert provenance["fixture_set"] == "baseline"
    assert provenance["version_id"] == "rag_eval_kb-baseline-2026-09-18"
    assert provenance["dataset_version"]
    assert provenance["git_sha"]


def test_prompt_candidate_snapshot_is_not_replaced_by_current_active_version():
    config = EvalConfig(
        module="rag",
        selection="pr_baseline",
        prompt_versions={"test.prompt": "2"},
        release_id="rel-1",
    )

    provenance = build_eval_provenance(config, _report(config))

    assert provenance["release_id"] == "rel-1"
    assert provenance["prompt_snapshot"]["test.prompt"] == "2"


def test_provenance_uses_report_scope_over_current_defaults():
    config = EvalConfig(module="rag", selection="expanded_100", multiquery=True)
    report = _report(config)
    report.metadata["evaluation_scope"]["fixture_set"] = "expanded_100"

    provenance = build_eval_provenance(config, report)

    assert provenance["suite"] == "expanded_100"
    assert provenance["fixture_set"] == "expanded_100"
    assert provenance["multiquery"] is False


def test_eval_config_defaults_keep_legacy_callers_compatible():
    config = EvalConfig(module="rag")

    assert config.prompt_versions == {}
    assert config.release_id is None
