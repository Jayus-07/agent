from contextlib import contextmanager

import pytest

from backend.evaluation_v2 import repository


class FakeCursor:
    def __init__(self, *, status, total_cases, completed, passed, existing_gate=None):
        self.status = status
        self.total_cases = total_cases
        self.completed = completed
        self.passed = passed
        self.existing_gate = existing_gate
        self.row = None
        self.statements = []
        self.audit_actions = []

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        self.statements.append((sql, params))
        if "SELECT job_status, total_cases" in sql:
            self.row = {"job_status": self.status, "total_cases": self.total_cases}
        elif "SELECT job_status FROM evaluation_v2.eval_runs" in sql:
            self.row = {"job_status": self.status}
        elif "SELECT COUNT(*) AS completed" in sql:
            self.row = {"completed": self.completed, "passed": self.passed}
        elif "FROM evaluation_v2.eval_gate_decisions" in sql and "ORDER BY decided_at" in sql:
            self.row = self.existing_gate
        elif "INSERT INTO evaluation_v2.eval_gate_decisions" in sql:
            self.row = {
                "decision_id": "decision-1",
                "run_id": "run-1",
                "policy_hash": params[2],
                "verdict": params[3],
                "reasons_json": [{"code": "policy_satisfied"}],
                "decided_by": params[5],
            }
        elif "INSERT INTO evaluation_v2.eval_audit_events" in sql:
            self.audit_actions.append(params[2])
            self.row = None
        else:
            self.row = None

    def fetchone(self):
        return self.row


@contextmanager
def _fake_transaction(cursor):
    yield None, cursor


def _call_complete(monkeypatch, cursor, *, reasons=None):
    monkeypatch.setattr(repository, "_transaction", lambda _tenant: _fake_transaction(cursor))
    return repository.complete_run_with_gate(
        "tenant-a",
        "user:reviewer",
        "run-1",
        policy_hash="a" * 64,
        verdict="PASS",
        reasons=reasons or [{"code": "policy_satisfied"}],
    )


def test_run_completion_and_gate_are_written_in_one_transaction(monkeypatch):
    cursor = FakeCursor(status="aggregating", total_cases=2, completed=2, passed=1)

    decision = _call_complete(monkeypatch, cursor)

    sql = [statement for statement, _params in cursor.statements]
    assert decision["decision_id"] == "decision-1"
    assert any("INSERT INTO evaluation_v2.eval_gate_decisions" in item for item in sql)
    assert any("UPDATE evaluation_v2.eval_runs AS r SET job_status = 'completed'" in item for item in sql)
    assert cursor.audit_actions == ["run.status_changed", "run.gate_decided"]


def test_run_cannot_complete_without_every_case_result(monkeypatch):
    cursor = FakeCursor(status="aggregating", total_cases=2, completed=1, passed=1)

    with pytest.raises(repository.RepositoryConflictError, match="逐例结果未完整持久化"):
        _call_complete(monkeypatch, cursor)

    assert not any("INSERT INTO evaluation_v2.eval_gate_decisions" in sql for sql, _ in cursor.statements)
    assert not any("UPDATE evaluation_v2.eval_runs" in sql for sql, _ in cursor.statements)


def test_completed_run_gate_replay_is_idempotent(monkeypatch):
    existing = {
        "decision_id": "decision-existing",
        "policy_hash": "a" * 64,
        "verdict": "PASS",
        "reasons_json": [{"code": "policy_satisfied"}],
        "decided_by": "user:reviewer",
    }
    cursor = FakeCursor(
        status="completed",
        total_cases=2,
        completed=2,
        passed=1,
        existing_gate=existing,
    )

    decision = _call_complete(monkeypatch, cursor)

    assert decision == existing
    assert not any("INSERT INTO evaluation_v2.eval_gate_decisions" in sql for sql, _ in cursor.statements)
    assert not any("UPDATE evaluation_v2.eval_runs" in sql for sql, _ in cursor.statements)


def test_completed_run_rejects_a_different_gate_replay(monkeypatch):
    cursor = FakeCursor(
        status="completed",
        total_cases=2,
        completed=2,
        passed=1,
        existing_gate={
            "decision_id": "decision-existing",
            "policy_hash": "b" * 64,
            "verdict": "PASS",
            "reasons_json": [{"code": "policy_satisfied"}],
            "decided_by": "user:reviewer",
        },
    )

    with pytest.raises(repository.RepositoryConflictError, match="已有不同的 Gate 裁决"):
        _call_complete(monkeypatch, cursor)

    assert not any("INSERT INTO evaluation_v2.eval_gate_decisions" in sql for sql, _ in cursor.statements)


def test_failed_run_and_invalid_gate_are_written_in_one_transaction(monkeypatch):
    cursor = FakeCursor(status="running", total_cases=2, completed=0, passed=0)
    monkeypatch.setattr(repository, "_transaction", lambda _tenant: _fake_transaction(cursor))

    decision = repository.terminate_run_with_gate(
        "tenant-a",
        "user:worker",
        "run-1",
        status="failed",
        invalid_reason="corpus_snapshot_missing",
        policy_hash="b" * 64,
        verdict="INVALID",
        reasons=[{"code": "corpus_snapshot_missing"}],
    )

    sql = [statement for statement, _params in cursor.statements]
    assert decision["decision_id"] == "decision-1"
    assert any("UPDATE evaluation_v2.eval_runs AS r SET job_status" in item for item in sql)
    assert any("INSERT INTO evaluation_v2.eval_gate_decisions" in item for item in sql)
    assert cursor.audit_actions == ["run.status_changed", "run.gate_decided"]
