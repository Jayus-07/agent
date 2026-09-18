from __future__ import annotations

import json
from pathlib import Path

from backend.audit.rag20k.decisions import (
    validate_decisions,
    summarize_decisions,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _confirmed(id_: str = "Q1") -> dict:
    return {
        "id": id_,
        "decision": "已确认",
        "owner": "业务负责人",
        "decided_at": "2026-09-18",
        "evidence_refs": ["docs/report.md#section"],
        "status": "confirmed",
    }


def test_decisions_require_owner_date_and_evidence() -> None:
    """confirmed 状态缺签字要素时必须逐字段报错，防止暂定假设冒充结论。"""
    issues = validate_decisions({"decisions": [{"id": "Q1", "status": "confirmed"}]})

    assert {issue.field for issue in issues} >= {"owner", "decided_at", "evidence_refs"}


def test_blocked_decision_prevents_phase0_pass() -> None:
    summary = summarize_decisions(load_fixture("q1_q10_with_q6_blocked.json"))

    assert summary.phase0_ready is False
    assert summary.blocked_ids == ["Q6"]


def test_blocked_decision_requires_blocking_reason() -> None:
    """blocked 不写阻断原因会让"为什么不能确认"无法审计。"""
    data = {"decisions": [{**_confirmed(), "status": "blocked"}]}
    del data["decisions"][0]["owner"]
    data["decisions"][0]["owner"] = None

    issues = validate_decisions(data)

    assert any(issue.field == "blocking_reason" for issue in issues)


def test_duplicate_ids_and_unknown_status_are_rejected() -> None:
    data = {"decisions": [_confirmed(), _confirmed("Q2"), {**_confirmed("Q2"), "status": "maybe"}]}

    issues = validate_decisions(data)

    assert any(issue.field == "id" and issue.message and "Q2" in issue.message for issue in issues)
    assert any(issue.field == "status" for issue in issues)


def test_all_ten_questions_must_be_present() -> None:
    """Q1—Q10 少任何一项都不得宣称阶段 0 决策契约完整。"""
    data = {"decisions": [_confirmed(f"Q{i}") for i in range(1, 10)]}

    issues = validate_decisions(data)

    assert any(issue.field == "id" and "Q10" in issue.message for issue in issues)


def test_real_decisions_file_is_blocked_and_schema_clean() -> None:
    """真实决策文件必须 schema 干净且如实：签字变化时同步更新本钉住集合。

    2026-09-19 Jayus-07 确认 Q1—Q4/Q8—Q9；Q5/Q6/Q7/Q10 等外部材料，保持 blocked。
    """
    summary = summarize_decisions(
        json.loads(
            Path(
                "docs/evidence/rag20k/phase0/decisions/Q1-Q10.json"
            ).read_text(encoding="utf-8")
        )
    )

    assert summary.issues == []
    assert summary.blocked_ids == ["Q5", "Q6", "Q7", "Q10"]
    assert summary.confirmed_ids == ["Q1", "Q2", "Q3", "Q4", "Q8", "Q9"]
    assert summary.phase0_ready is False


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    """blocked 退出 2 并点名 ID；schema 错误退出 1；全部确认退出 0。"""
    from backend.scripts.validate_rag20k_decisions import run

    all_confirmed = tmp_path / "ok.json"
    all_confirmed.write_text(
        json.dumps({"decisions": [_confirmed(f"Q{i}") for i in range(1, 11)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    blocked = tmp_path / "blocked.json"
    blocked.write_text(
        json.dumps(
            {
                "decisions": [
                    *[ _confirmed(f"Q{i}") for i in range(1, 6) ],
                    {**_confirmed("Q6"), "status": "blocked", "blocking_reason": "供应商配额未取得"},
                    *[ _confirmed(f"Q{i}") for i in range(7, 11) ],
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")

    assert run([str(all_confirmed)]) == 0
    assert run([str(blocked)]) == 2
    assert "Q6" in capsys.readouterr().out
    assert run([str(broken)]) == 1
    assert run([str(tmp_path / "missing.json")]) == 1
