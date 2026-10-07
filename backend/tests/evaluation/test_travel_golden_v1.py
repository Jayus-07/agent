"""travel Golden Set v1 的固定版本与配额守护。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_DATASET = _ROOT / "evaluation" / "datasets" / "travel" / "golden_v1.jsonl"
_MANIFEST = _ROOT / "evaluation" / "datasets" / "travel" / "golden_v1.manifest.json"


def test_travel_golden_v1_has_fixed_50_case_mix():
    rows = [json.loads(line) for line in _DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))

    assert len(rows) == 50
    assert len({row["id"] for row in rows}) == 50
    assert {"question", "expected_doc", "expected_answer", "type"} <= set(rows[0])
    counts = {kind: sum(row["type"] == kind for row in rows)
              for kind in {row["type"] for row in rows}}
    assert counts == {
        "poi": 20,
        "food": 10,
        "route": 5,
        "transit": 3,
        "no_answer": 8,
        "permission_negative": 4,
    }
    assert all(not row["expected_doc"] for row in rows
               if row["type"] in {"no_answer", "permission_negative"})
    # P0-03：行尾归一化后计算——与 .gitattributes(*.jsonl eol=lf) 的入库字节一致
    digest = hashlib.sha256(_DATASET.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert manifest["version"] == "travel-golden-v1"
    assert manifest["case_count"] == 50
    assert manifest["content_sha256"] == digest


def test_rag_regression_workflows_cover_pr_and_scheduled_gates():
    workflows = _ROOT.parent / ".github" / "workflows"
    smoke = (workflows / "rag_smoke.yml").read_text(encoding="utf-8")
    regression = (workflows / "rag_regression.yml").read_text(encoding="utf-8")

    assert "pull_request:" in smoke
    assert "RAG Smoke (8 cases)" in smoke
    for path_prefix in ("backend/rag/", "backend/evaluation/", "backend/config/",
                         "backend/prompts/", "pyproject.toml"):
        assert path_prefix in smoke
    assert "schedule:" in regression
    assert 'cron: "0 18 * * *"' in regression
    for selection in ("regression", "ci_golden", "expanded_100", "scale_20k"):
        assert selection in regression
