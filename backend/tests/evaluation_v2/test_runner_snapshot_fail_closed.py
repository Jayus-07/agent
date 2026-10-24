from types import SimpleNamespace

import pytest

from backend.evaluation import corpus_snapshots
from backend.evaluation_v2 import runner


def test_missing_fixed_corpus_snapshot_fails_run_and_records_invalid_gate(monkeypatch):
    status = {"value": "queued"}
    status_changes = []
    terminal_decisions = []
    case_id = "d21af908-53ce-4a8e-8eae-5391a7c7416a"
    run = {
        "run_id": "a52d012e-59b6-467e-aad2-5a40b8337d17",
        "job_status": "queued",
        "suite_version_id": "suite-version-1",
        "artifact_uri": "rag-snapshot:missing-snapshot",
        "corpus_hash": "a" * 64,
        "kb_id": "kb-1",
        "snapshot_json": {
            "suite_hash": "suite-hash",
            "dataset_hash": "dataset-hash",
            "runtime_environment": {"rag_mode": "remote", "models": {}},
            "cases": [{"case_id": case_id, "case_revision": 1, "content_hash": "case-hash"}],
        },
    }
    definition = {
        "module": "rag",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "cases": [{"case_id": case_id, "case_revision": 1, "revision_hash": "case-hash"}],
    }

    monkeypatch.setattr(
        runner.repository,
        "get_run",
        lambda _tenant_id, _run_id: {**run, "job_status": status["value"]},
    )
    monkeypatch.setattr(
        runner.repository,
        "set_run_status",
        lambda _tenant_id, _actor, _run_id, next_status, **_kwargs: (
            status.update(value=next_status), status_changes.append(next_status)
        ),
    )
    monkeypatch.setattr(
        runner.repository,
        "get_run_definition",
        lambda _tenant_id, _suite_version_id: definition,
    )
    monkeypatch.setattr(
        runner.repository,
        "terminate_run_with_gate",
        lambda _tenant_id, _actor, _run_id, **decision: terminal_decisions.append(decision),
    )

    def missing_snapshot(_snapshot_id):
        raise FileNotFoundError("fixture intentionally has no corpus artifact")

    monkeypatch.setattr(corpus_snapshots, "load", missing_snapshot)

    with pytest.raises(runner.CorpusSnapshotMissingError):
        runner.execute_run(
            run["run_id"],
            tenant_id="default",
            actor="user:eval-admin",
            boundary=lambda: None,
        )

    assert status_changes == ["preparing"]
    assert status["value"] == "preparing"
    assert terminal_decisions == [{
        "status": "failed",
        "invalid_reason": "corpus_snapshot_missing",
        "policy_hash": runner.repository.content_hash({"terminal": "corpus_snapshot_missing"}),
        "verdict": "INVALID",
        "reasons": [{"code": "corpus_snapshot_missing"}],
    }]


def test_task_cancellation_uses_atomic_run_and_gate_termination(monkeypatch):
    from backend.orchestration.checkpoint import TaskCancelled

    run = {
        "run_id": "a52d012e-59b6-467e-aad2-5a40b8337d17",
        "job_status": "queued",
        "suite_version_id": "suite-version-1",
        "snapshot_json": {},
    }
    status_changes = []
    terminal_decisions = []
    monkeypatch.setattr(runner.repository, "get_run", lambda *_: run)
    monkeypatch.setattr(
        runner.repository,
        "set_run_status",
        lambda _tenant, _actor, _run_id, status, **_kwargs: status_changes.append(status),
    )
    monkeypatch.setattr(
        runner.repository,
        "terminate_run_with_gate",
        lambda _tenant, _actor, _run_id, **decision: terminal_decisions.append(decision),
    )
    monkeypatch.setattr(
        runner.repository,
        "record_gate_decision",
        lambda *_args, **_kwargs: pytest.fail("取消终态与 Gate 不得分开提交"),
    )

    result = runner.execute_run(
        run["run_id"],
        tenant_id="default",
        actor="user:eval-admin",
        boundary=lambda: (_ for _ in ()).throw(TaskCancelled(run["run_id"])),
    )

    assert result["status"] == "CANCELLED"
    assert result["gate_verdict"] == "INVALID"
    assert status_changes == ["preparing"]
    assert terminal_decisions[0]["status"] == "cancelled"
    assert terminal_decisions[0]["verdict"] == "INVALID"
    assert terminal_decisions[0]["reasons"] == [{"code": "cancelled"}]


def test_successful_runner_completes_run_with_gate_atomically(monkeypatch):
    from backend.evaluation import managed_execution, storage
    from backend.evaluation_v2 import runtime_environment

    case_id = "d21af908-53ce-4a8e-8eae-5391a7c7416a"
    run = {
        "run_id": "a52d012e-59b6-467e-aad2-5a40b8337d17",
        "job_status": "queued",
        "suite_version_id": "suite-version-1",
        "artifact_uri": "rag-snapshot:snapshot-1",
        "corpus_hash": "a" * 64,
        "kb_id": "kb-1",
        "created_by": "user:eval-admin",
        "snapshot_json": {
            "suite_hash": "suite-hash",
            "dataset_hash": "dataset-hash",
            "runtime_environment": {"rag_mode": "remote", "models": {}},
            "cases": [{"case_id": case_id, "case_revision": 1, "content_hash": "case-hash"}],
        },
    }
    definition = {
        "module": "rag",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "cases": [{"case_id": case_id, "case_revision": 1, "revision_hash": "case-hash"}],
        "minimum_valid_samples": 1,
        "metric_policy": {"min_pass_rate": 0.9},
        "security_policy": {},
    }
    report = SimpleNamespace(
        metadata={"run_validity": {"validity": "VALID"}},
        results=[SimpleNamespace(
            case_id=case_id,
            status="pass",
            metrics={"recall@5": 1.0},
            actual={"trace_id": "trace-1"},
            error_stage=None,
            duration_ms=12,
        )],
    )
    state = {"status": "queued", "report_loads": 0}
    calls = []

    def get_run(_tenant, _run_id):
        return {**run, "job_status": state["status"]}

    def set_run_status(_tenant, _actor, _run_id, status, **_kwargs):
        assert status != "completed", "Run 完成与 Gate 必须在同一事务中提交"
        state["status"] = status
        calls.append(("status", status))

    def load_report(_run_id):
        state["report_loads"] += 1
        if state["report_loads"] == 1:
            raise FileNotFoundError
        return report, {}

    def append_results(_tenant, _actor, _run_id, results):
        calls.append(("results", results))

    def complete_with_gate(_tenant, _actor, _run_id, **decision):
        assert state["status"] == "aggregating"
        calls.append(("complete_with_gate", decision))
        state["status"] = "completed"

    monkeypatch.setattr(runner.repository, "get_run", get_run)
    monkeypatch.setattr(runner.repository, "get_run_definition", lambda *_: definition)
    monkeypatch.setattr(runner.repository, "list_gate_decisions", lambda *_: [])
    monkeypatch.setattr(runner.repository, "set_run_status", set_run_status)
    monkeypatch.setattr(runner.repository, "append_run_results", append_results)
    monkeypatch.setattr(runner.repository, "complete_run_with_gate", complete_with_gate)
    monkeypatch.setattr(runner.repository, "record_gate_decision", lambda *_a, **_kw: pytest.fail("成功路径应使用原子 Gate 完成"))
    monkeypatch.setattr(runner.repository, "terminate_run_with_gate", lambda *_a, **_kw: pytest.fail("成功路径不得走失败终态写入"))
    monkeypatch.setattr(runtime_environment, "assert_runtime_environment", lambda _expected: None)
    monkeypatch.setattr(runner, "_refresh_model_registry", lambda: None)
    monkeypatch.setattr(corpus_snapshots, "load", lambda _snapshot: {
        "content_hash": "a" * 64,
        "kb_id": "kb-1",
        "documents": [],
    })
    monkeypatch.setattr(runner, "_legacy_dataset", lambda *_: "binding-1")
    def execute_binding(*_args, **kwargs):
        calls.append(("execute", None))
        kwargs["boundary"]()

    monkeypatch.setattr(managed_execution, "execute_binding", execute_binding)
    monkeypatch.setattr(storage, "load_report", load_report)

    result = runner.execute_run(
        run["run_id"], tenant_id="default", actor="user:eval-admin", boundary=lambda: None
    )

    assert result["gate_verdict"] == "PASS"
    assert state["status"] == "completed"
    assert [kind for kind, _payload in calls][-3:] == ["status", "results", "complete_with_gate"]


def test_runtime_environment_change_fails_run_before_retrieval(monkeypatch):
    from backend.evaluation_v2 import runtime_environment

    run_id = "a52d012e-59b6-467e-aad2-5a40b8337d17"
    status = {"value": "queued"}
    terminal_decisions = []
    run = {
        "run_id": run_id,
        "job_status": "queued",
        "suite_version_id": "suite-version-1",
        "artifact_uri": "rag-snapshot:snapshot-1",
        "corpus_hash": "a" * 64,
        "kb_id": "kb-1",
        "snapshot_json": {
            "suite_hash": "suite-hash",
            "dataset_hash": "dataset-hash",
            "runtime_environment": {"rag_mode": "remote", "models": {"embedding": "old"}},
            "cases": [{
                "case_id": "d21af908-53ce-4a8e-8eae-5391a7c7416a",
                "case_revision": 1,
                "content_hash": "case-hash",
            }],
        },
    }
    definition = {
        "module": "rag",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "cases": [{
            "case_id": "d21af908-53ce-4a8e-8eae-5391a7c7416a",
            "case_revision": 1,
            "revision_hash": "case-hash",
        }],
    }

    def get_run(_tenant, _run_id):
        return {**run, "job_status": status["value"]}

    def set_status(_tenant, _actor, _run_id, value, **_kwargs):
        status["value"] = value

    def reject_drift(_expected):
        raise runtime_environment.RuntimeEnvironmentMismatchError("runtime_environment_changed")

    monkeypatch.setattr(runner.repository, "get_run", get_run)
    monkeypatch.setattr(runner.repository, "get_run_definition", lambda *_: definition)
    monkeypatch.setattr(runner.repository, "set_run_status", set_status)
    monkeypatch.setattr(
        runner.repository,
        "terminate_run_with_gate",
        lambda _tenant, _actor, _run_id, **decision: terminal_decisions.append(decision),
    )
    monkeypatch.setattr(corpus_snapshots, "load", lambda _snapshot: {
        "content_hash": "a" * 64,
        "kb_id": "kb-1",
        "documents": [],
    })
    monkeypatch.setattr(runtime_environment, "assert_runtime_environment", reject_drift)
    monkeypatch.setattr(runner, "_refresh_model_registry", lambda: None)
    monkeypatch.setattr(runner, "_legacy_dataset", lambda *_: pytest.fail("环境漂移时不得构建检索输入"))

    with pytest.raises(runtime_environment.RuntimeEnvironmentMismatchError):
        runner.execute_run(
            run_id, tenant_id="default", actor="user:eval-admin", boundary=lambda: None
        )

    assert status["value"] == "preparing"
    assert terminal_decisions[0]["status"] == "failed"
    assert terminal_decisions[0]["verdict"] == "INVALID"
    assert terminal_decisions[0]["invalid_reason"] == "runtime_environment_changed"
    assert terminal_decisions[0]["reasons"] == [{"code": "runtime_environment_changed"}]
