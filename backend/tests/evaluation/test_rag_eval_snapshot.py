"""RAG baseline snapshot 的稳定身份契约。"""

from __future__ import annotations

import pytest

from backend.evaluation.runners._common import build_scope_metadata_filter
from backend.evaluation.runners.rag import build_eval_scope


def test_baseline_snapshot_has_stable_scope():
    from backend.evaluation.datasets.rag.snapshots import load_rag_snapshot

    snapshot = load_rag_snapshot("baseline")

    assert snapshot["kb_id"] == "rag_eval_kb"
    assert snapshot["fixture_set"] == "baseline"
    assert snapshot["version_id"]
    assert snapshot["cases_path"].endswith("pr_baseline.json")


def test_unknown_snapshot_fails_without_directory_fallback():
    from backend.evaluation.datasets.rag.snapshots import load_rag_snapshot

    with pytest.raises(ValueError, match="snapshot"):
        load_rag_snapshot("does-not-exist")


def test_scope_and_filter_carry_snapshot_version():
    scope = build_eval_scope(
        kb_id="rag_eval_kb",
        fixture_set="baseline",
        version_id="rag_eval_kb-baseline-2026-09-18",
        multiquery=False,
    )

    assert scope.as_dict()["version_id"] == "rag_eval_kb-baseline-2026-09-18"
    assert build_scope_metadata_filter(
        scope.kb_id,
        fixture_set=scope.fixture_set,
        version_id=scope.version_id,
    ) == {
        "kb_id": "rag_eval_kb",
        "fixture_set": "baseline",
        "version_id": "rag_eval_kb-baseline-2026-09-18",
    }
