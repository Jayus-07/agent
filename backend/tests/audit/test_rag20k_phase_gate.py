from __future__ import annotations

import json
from pathlib import Path

from backend.audit.rag20k.phase_gate import evaluate_phase0

_EVIDENCE_IDS = {"baseline", "decisions", "corpus", "golden", "evaluation", "risk_register"}


def _confirmed(id_: str) -> dict:
    return {
        "id": id_,
        "decision": "已确认",
        "owner": "业务负责人",
        "decided_at": "2026-09-18",
        "evidence_refs": ["docs/report.md#section"],
        "status": "confirmed",
    }


def _write_passing_evidence(root: Path) -> None:
    (root / "baseline" / "0123456789abcdef").mkdir(parents=True)
    (root / "baseline" / "0123456789abcdef" / "baseline-manifest.json").write_text(
        json.dumps({"capture_id": "0123456789abcdef", "blocking_items": [], "docker": {"status": "ok"}}),
        encoding="utf-8",
    )
    (root / "decisions").mkdir(parents=True)
    (root / "decisions" / "Q1-Q10.json").write_text(
        json.dumps({"decisions": [_confirmed(f"Q{i}") for i in range(1, 11)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    for name in ("corpus", "golden"):
        (root / name).mkdir(parents=True)
        (root / name / "validation-summary.json").write_text(
            json.dumps({"status": "ok"}), encoding="utf-8"
        )
    (root / "evaluation").mkdir(parents=True)
    (root / "evaluation" / "reproducibility.json").write_text(
        json.dumps({"comparable": True, "passed": True}), encoding="utf-8"
    )
    (root / "risk-register.md").write_text("# 风险登记册\n", encoding="utf-8")


def test_phase0_gate_lists_missing_evidence(tmp_path: Path) -> None:
    result = evaluate_phase0(tmp_path)

    assert result.passed is False
    assert set(result.missing) == _EVIDENCE_IDS
    assert result.failed == []


def test_phase0_gate_passes_only_with_full_evidence(tmp_path: Path) -> None:
    _write_passing_evidence(tmp_path)

    result = evaluate_phase0(tmp_path)

    assert result.passed is True
    assert result.missing == []
    assert result.failed == []


def test_phase0_gate_flags_blocked_decisions(tmp_path: Path) -> None:
    _write_passing_evidence(tmp_path)
    decisions = tmp_path / "decisions" / "Q1-Q10.json"
    data = json.loads(decisions.read_text(encoding="utf-8"))
    data["decisions"][5] = {**data["decisions"][5], "status": "blocked", "blocking_reason": "配额未取得"}
    data["decisions"][5].pop("owner")
    decisions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = evaluate_phase0(tmp_path)

    assert result.passed is False
    assert "decisions" in result.failed


def test_phase0_gate_flags_blocked_or_failed_summaries(tmp_path: Path) -> None:
    _write_passing_evidence(tmp_path)
    (tmp_path / "corpus" / "validation-summary.json").write_text(
        json.dumps({"status": "blocked"}), encoding="utf-8"
    )

    result = evaluate_phase0(tmp_path)

    assert result.passed is False
    assert "corpus" in result.failed
    assert "corpus" not in result.missing


def test_phase0_gate_reports_schema_errors(tmp_path: Path) -> None:
    _write_passing_evidence(tmp_path)
    (tmp_path / "decisions" / "Q1-Q10.json").write_text("{broken", encoding="utf-8")

    result = evaluate_phase0(tmp_path)

    assert result.passed is False
    assert any("decisions" in error for error in result.schema_errors)


def test_phase0_gate_requires_every_baseline_capture_clean(tmp_path: Path) -> None:
    """多份 capture 时任何一份带阻断项都会阻止出口；防止留一份脏证据蒙混。"""
    _write_passing_evidence(tmp_path)
    stale_dir = tmp_path / "baseline" / "ffffffffffffffff"
    stale_dir.mkdir()
    (stale_dir / "baseline-manifest.json").write_text(
        json.dumps({"capture_id": "f" * 16, "blocking_items": ["Docker 镜像 web 缺少不可变 RepoDigest"], "docker": {"status": "blocked"}}),
        encoding="utf-8",
    )

    result = evaluate_phase0(tmp_path)

    assert result.passed is False
    assert "baseline" in result.failed
