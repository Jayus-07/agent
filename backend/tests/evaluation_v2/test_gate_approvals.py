from contextlib import contextmanager

import pytest

from backend.evaluation_v2 import repository


class ApprovalCursor:
    def __init__(self, *, run=None, existing=None, latest=None, inserted=None):
        self.run = run or {"job_status": "completed", "gate_verdict": "PASS"}
        self.existing = existing
        self.latest = latest or {"decision_id": "gate-current", "verdict": "PASS"}
        self.inserted = inserted or {
            "approval_id": "approval-new",
            "run_id": "run-1",
            "gate_decision_id": "gate-current",
            "request_id": "request-1234",
            "decision": "approved",
            "release_reference": "release:v2.4.1",
            "rationale": "已核验回归、安全样本与发布范围。",
            "decided_by": "user:alice",
            "decided_at": "2026-10-24T00:00:00Z",
        }
        self.pending = None
        self.statements = []
        self.insert_count = 0

    def execute(self, query, params):
        normalized = " ".join(query.split())
        self.statements.append((normalized, params))
        if "SELECT job_status, gate_verdict" in normalized:
            self.pending = self.run
        elif "FROM evaluation_v2.eval_gate_approvals" in normalized:
            self.pending = self.existing
        elif "SELECT decision_id, verdict" in normalized:
            self.pending = self.latest
        elif "INSERT INTO evaluation_v2.eval_gate_approvals" in normalized:
            self.insert_count += 1
            self.pending = self.inserted
        else:
            raise AssertionError(f"未预期 SQL: {normalized}")

    def fetchone(self):
        row, self.pending = self.pending, None
        return row


@contextmanager
def transaction_for(cursor):
    yield object(), cursor


def request_kwargs():
    return {
        "gate_decision_id": "gate-current",
        "request_id": "request-1234",
        "decision": "approved",
        "release_reference": "release:v2.4.1",
        "rationale": "已核验回归、安全样本与发布范围。",
    }


def install_transaction(monkeypatch, cursor):
    monkeypatch.setattr(repository, "_transaction", lambda _tenant_id: transaction_for(cursor))


def test_gate_approval_requires_completed_run_and_latest_pass_decision(monkeypatch):
    cursor = ApprovalCursor()
    install_transaction(monkeypatch, cursor)
    audit = []
    monkeypatch.setattr(repository, "_record_audit", lambda *args, **kwargs: audit.append((args, kwargs)))

    result = repository.record_gate_approval("tenant-a", "user:alice", "run-1", **request_kwargs())

    assert result["approval_id"] == "approval-new"
    assert cursor.insert_count == 1
    assert any("FOR UPDATE" in statement for statement, _ in cursor.statements)
    assert len(audit) == 1
    assert audit[0][0][3:6] == ("run.release_approved", "run", "run-1")
    assert audit[0][1]["details"]["gate_decision_id"] == "gate-current"


@pytest.mark.parametrize(
    ("run", "latest", "gate_decision_id", "message"),
    [
        (
            {"job_status": "running", "gate_verdict": "PASS"},
            {"decision_id": "gate-current", "verdict": "PASS"},
            "gate-current",
            "已完成且 Gate 为 PASS",
        ),
        (
            {"job_status": "completed", "gate_verdict": "FAIL"},
            {"decision_id": "gate-current", "verdict": "FAIL"},
            "gate-current",
            "已完成且 Gate 为 PASS",
        ),
        (
            {"job_status": "completed", "gate_verdict": "PASS"},
            {"decision_id": "gate-newer", "verdict": "PASS"},
            "gate-current",
            "裁决已变化",
        ),
        (
            {"job_status": "completed", "gate_verdict": "PASS"},
            {"decision_id": "gate-current", "verdict": "FAIL"},
            "gate-current",
            "最新 Gate 裁决不是 PASS",
        ),
    ],
)
def test_gate_approval_rejects_non_pass_or_stale_decisions(
    monkeypatch, run, latest, gate_decision_id, message
):
    cursor = ApprovalCursor(run=run, latest=latest)
    install_transaction(monkeypatch, cursor)
    kwargs = request_kwargs()
    kwargs["gate_decision_id"] = gate_decision_id

    with pytest.raises(repository.RepositoryConflictError, match=message):
        repository.record_gate_approval("tenant-a", "user:alice", "run-1", **kwargs)

    assert cursor.insert_count == 0


def test_gate_approval_idempotent_replay_does_not_insert_or_audit_again(monkeypatch):
    prior = {
        **ApprovalCursor().inserted,
        "request_hash": repository.content_hash({
            "run_id": "run-1",
            "gate_decision_id": "gate-current",
            "decision": "approved",
            "release_reference": "release:v2.4.1",
            "rationale": "已核验回归、安全样本与发布范围。",
        }),
    }
    cursor = ApprovalCursor(existing=prior)
    install_transaction(monkeypatch, cursor)
    audit = []
    monkeypatch.setattr(repository, "_record_audit", lambda *args, **kwargs: audit.append((args, kwargs)))

    result = repository.record_gate_approval("tenant-a", "user:alice", "run-1", **request_kwargs())

    assert result == prior
    assert cursor.insert_count == 0
    assert audit == []


def test_gate_approval_idempotency_key_cannot_be_reused_for_other_content(monkeypatch):
    cursor = ApprovalCursor(existing={**ApprovalCursor().inserted, "request_hash": "0" * 64})
    install_transaction(monkeypatch, cursor)

    with pytest.raises(repository.RepositoryConflictError, match="幂等键已用于不同请求"):
        repository.record_gate_approval("tenant-a", "user:alice", "run-1", **request_kwargs())

    assert cursor.insert_count == 0
