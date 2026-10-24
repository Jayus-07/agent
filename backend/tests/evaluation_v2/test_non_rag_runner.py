from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend.evaluation.models import EvalResult
from backend.evaluation_v2 import runner


class _ReviewCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return next(self.rows, None)


def test_non_rag_case_projection_preserves_domain_input_and_metadata():
    case_id = "d21af908-53ce-4a8e-8eae-5391a7c7416a"
    definition = {
        "cases": [{
            "case_id": case_id,
            "input_json": {
                "turns": [
                    {"role": "user", "text": "之前的问题"},
                    {"role": "assistant", "text": "客服回复"},
                    {"role": "user", "text": "最后的问题"},
                ],
            },
            "expected_json": {"target": "knowledge"},
            "metadata_json": {"tier": "core", "source": "golden"},
        }],
    }

    cases = runner._build_non_rag_cases(definition, "cs")

    assert len(cases) == 1
    assert cases[0].question == "最后的问题"
    assert cases[0].expected == {"target": "knowledge"}
    assert cases[0].metadata == {"tier": "core", "source": "golden"}


def test_non_rag_run_uses_registered_offline_runner_and_persists_gate(monkeypatch):
    from backend.evaluation import registry, storage

    run_id = "a52d012e-59b6-467e-aad2-5a40b8337d17"
    case_id = "d21af908-53ce-4a8e-8eae-5391a7c7416a"
    environment = {
        "runner": "legacy-travel-provider-v1",
        "module": "travel-provider",
        "execution_semantics": "system_quality",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
    }
    snapshot = {
        "module": "travel-provider",
        "execution_semantics": "system_quality",
        "environment": environment,
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "cases": [{"case_id": case_id, "case_revision": 1, "content_hash": "case-hash"}],
    }
    run = {
        "run_id": run_id,
        "job_status": "queued",
        "suite_version_id": "suite-version-1",
        "snapshot_json": snapshot,
    }
    definition = {
        "module": "travel-provider",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "minimum_valid_samples": 1,
        "metric_policy": {"min_pass_rate": 1.0},
        "security_policy": {},
        "cases": [{
            "case_id": case_id,
            "case_revision": 1,
            "revision_hash": "case-hash",
            "input_json": {"query": "provider 探针"},
            "expected_json": {"status": "success"},
            "metadata_json": {"probe": "place_success"},
        }],
    }
    state = {"status": "queued"}
    calls = []
    report_store = {}

    def get_run(_tenant, _run_id):
        return {**run, "job_status": state["status"]}

    def set_run_status(_tenant, _actor, _run_id, status, **_kwargs):
        state["status"] = status
        calls.append(("status", status))

    def complete_with_gate(_tenant, _actor, _run_id, **decision):
        calls.append(("complete", decision))
        state["status"] = "completed"

    monkeypatch.setattr(runner.repository, "get_run", get_run)
    monkeypatch.setattr(runner.repository, "get_run_definition", lambda *_: definition)
    monkeypatch.setattr(runner.repository, "list_gate_decisions", lambda *_: [])
    monkeypatch.setattr(runner.repository, "set_run_status", set_run_status)
    monkeypatch.setattr(runner.repository, "append_run_results", lambda *_args, **kwargs: calls.append(("results", kwargs)))
    monkeypatch.setattr(runner.repository, "complete_run_with_gate", complete_with_gate)
    monkeypatch.setattr(
        runner.repository,
        "terminate_run_with_gate",
        lambda *_args, **kwargs: calls.append(("terminated", kwargs)),
    )
    monkeypatch.setattr(storage, "load_report", lambda *_: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(storage, "persist_report", lambda report, run_id: report_store.update(report=report, run_id=run_id))

    def execute(cases, *, live=False, **_kwargs):
        assert live is False
        assert [item.id for item in cases] == [case_id]
        return [EvalResult(
            case_id=case_id,
            module="travel-provider",
            status="pass",
            expected={"status": "success"},
            actual={"status": "success"},
            metrics={"probe_ok": 1.0},
        )]

    monkeypatch.setattr(registry, "get_runner", lambda _module: SimpleNamespace(func=execute, needs_live=False))

    result = runner.execute_run(run_id, tenant_id="default", actor="user:eval-admin", boundary=lambda: None)

    assert result["gate_verdict"] == "PASS"
    assert result["cost_status"] == "not_applicable_no_model_call"
    assert report_store["run_id"] == run_id
    assert report_store["report"].metadata["model_calls"] is False
    assert state["status"] == "completed"
    assert calls[-1][0] == "complete"


def test_contract_only_runner_cannot_pass_quality_gate():
    report = SimpleNamespace(
        metadata={"run_validity": {"validity": "VALID"}},
        results=[SimpleNamespace(case_id="case-1", status="pass", error_stage=None)],
    )
    definition = {
        "module": "sql",
        "minimum_valid_samples": 1,
        "metric_policy": {"min_pass_rate": 0.0},
        "security_policy": {},
        "cases": [],
    }

    verdict, reasons = runner._gate(report, definition)

    assert verdict == "INVALID"
    assert reasons == [{
        "code": "runner_semantics_not_quality_evaluation",
        "module": "sql",
        "execution_semantics": "contract_only",
    }]


@pytest.mark.parametrize("operation", ["list", "record"])
def test_contract_only_run_cannot_be_used_for_human_quality_review(monkeypatch, operation):
    from backend.evaluation_v2 import repository

    run_id = "a52d012e-59b6-467e-aad2-5a40b8337d17"
    case_id = "d21af908-53ce-4a8e-8eae-5391a7c7416a"
    cursor = _ReviewCursor([{
        "job_status": "completed",
        "snapshot_json": {"execution_semantics": "contract_only"},
    }, {"result_status": "pass"}])

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)

    with pytest.raises(repository.RepositoryConflictError, match="契约检查结果不能作为人工质量评分样本"):
        if operation == "list":
            repository.list_human_reviews("default", run_id, case_id)
        else:
            repository.record_human_review(
                "default",
                "user:reviewer",
                run_id,
                case_id,
                evaluator_version_id="d21af908-53ce-4a8e-8eae-5391a7c7416b",
                request_id="contract-review-1234",
                label="correct",
                scores={"accuracy": 1.0},
                rationale="结构有效，但该运行仅执行契约检查。",
            )

    expected_queries = 2 if operation == "list" else 1
    assert len(cursor.executed) == expected_queries


def test_travel_provider_probes_do_not_patch_shared_provider_classes():
    from backend.evaluation.runners.travel_provider import _place_provider, _routing_provider
    from backend.providers.travel.live.tencent import TencentPlaceProvider, TencentRoutingProvider

    place_is_enabled = TencentPlaceProvider.is_enabled
    routing_is_enabled = TencentRoutingProvider.is_enabled

    place = _place_provider(lambda *_args, **_kwargs: None)
    routing = _routing_provider(lambda *_args, **_kwargs: {})

    assert place.is_enabled() is True
    assert routing.is_enabled() is True
    assert TencentPlaceProvider.is_enabled is place_is_enabled
    assert TencentRoutingProvider.is_enabled is routing_is_enabled


def test_travel_runner_restores_checkpointer_configuration_and_cached_graph(monkeypatch):
    import backend.travel.graph_builder as graph_builder
    from backend.config import travel as travel_config
    from backend.evaluation.runners.travel import _get_graph

    cached_graph = object()
    monkeypatch.setattr(travel_config, "TRAVEL_CHECKPOINTER_ENABLED", True)
    monkeypatch.setattr(travel_config, "TRAVEL_CHECKPOINTER_BACKEND", "redis")
    monkeypatch.setattr(graph_builder, "_travel_graph", cached_graph)
    monkeypatch.setattr(graph_builder, "get_travel_graph", lambda: object())

    _graph, restore = _get_graph(cross_turn=True)

    assert travel_config.TRAVEL_CHECKPOINTER_ENABLED is True
    assert travel_config.TRAVEL_CHECKPOINTER_BACKEND == "memory"
    assert graph_builder._travel_graph is None
    restore()

    assert travel_config.TRAVEL_CHECKPOINTER_ENABLED is True
    assert travel_config.TRAVEL_CHECKPOINTER_BACKEND == "redis"
    assert graph_builder._travel_graph is cached_graph
