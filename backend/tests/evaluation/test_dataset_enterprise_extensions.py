"""业务题集的版本、格式、固定语料、发布与任务控制边界。"""

from __future__ import annotations

import csv
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.evaluation.dataset import imports as assets
from backend.tests.api.test_evaluation_imports import package


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))


def imported(payload=None, approve=False):
    validated = assets.validate_upload("cases.json", json.dumps(payload or package()).encode(), {})
    assert validated["valid"], validated["errors"]
    result = assets.create_import(validated, "user:1")
    if approve:
        assets.write_once("reviews", result["import_id"], {"decision": "approved"})
    return result


def test_csv_mapping_schema_and_row_errors():
    payload = package()
    manifest = {
        **payload["manifest"],
        "csv_mapping": {"id": "题号", "question": "问题", "expected": "预期"},
        "case_schema": {"properties": {"question": {"minLength": 5}}},
    }
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["题号", "问题", "预期", "备注"])
    writer.writerow(["P1", "员工请假需要几天", json.dumps(payload["cases"][0]["expected"]), "明确不映射"])
    valid = assets.validate_upload("cases.csv", stream.getvalue().encode(), manifest)
    assert valid["valid"]
    assert valid["cases"][0]["question"] == "员工请假需要几天"
    bad = assets.validate_upload("cases.csv", stream.getvalue().replace("员工请假需要几天", "短").encode(), manifest)
    assert bad["errors"][0]["line"] == 2
    assert "Schema" in bad["errors"][0]["message"]


@pytest.mark.parametrize("schema", [{"$ref": "http://example.com"}, {"pattern": "(a+)+$"}, {"type": "not_a_type"}])
def test_unbounded_or_invalid_schema_rejected(schema):
    payload = package()
    payload["manifest"]["case_schema"] = schema
    assert not assets.validate_upload("cases.json", json.dumps(payload).encode(), {})["valid"]


def test_revision_archive_diff_label_and_split():
    from backend.evaluation.dataset.revisions import diff, revise, tag

    payload = package()
    second = deepcopy(payload["cases"][0])
    second["id"] = "P-002"
    payload["cases"].append(second)
    old = imported(payload, approve=True)
    changed = {**second, "question": "新的请假问题", "splits": ["regression"]}
    new = revise(old["import_id"], "1.1.0", {"P-001": None, "P-002": changed}, "user:1")
    assert new["review"]["decision"] == "pending_review"
    assert len(diff(old["import_id"], new["import_id"])["changes"]) == 2
    assert assets.approved_import(old["import_id"])["cases"][0].get("archived") is False
    with pytest.raises(FileNotFoundError):
        assets.approved_import(new["import_id"])
    assets.write_once("reviews", new["import_id"], {"decision": "approved"})
    tag(old["import_id"], "prod", "user:1")
    with pytest.raises(ValueError, match="标签"):
        tag(new["import_id"], "prod", "user:1")
    bid = "b" * 24
    assets.write_once(
        "bindings",
        bid,
        {"import_id": new["import_id"], "kb_id": "policy_general", "split": "regression", "mode": "answer"},
    )
    cases = assets.normalized_cases(bid)
    assert [c.id for c in cases] == ["P-002"]
    assert cases[0].metadata["generation_eval"] is True


def test_frozen_parent_and_table_do_not_read_current_store(monkeypatch):
    from backend.evaluation.corpus_snapshots import FrozenChunks
    from backend.rag.indexing.chunk_store import get_chunk_store, read_store_override

    record = {
        "fulltext": [
            {"doc_id": "d1", "chunk_id": "p1", "content": "旧父块", "chunk_metadata": json.dumps({"table_id": "t1"})}
        ]
    }
    frozen = FrozenChunks(record)
    token = read_store_override.set(frozen)
    try:
        assert get_chunk_store().get_by_chunk_ids(["p1"])[0]["content"] == "旧父块"
        assert get_chunk_store().get_by_table_ids("d1", ["t1"])[0]["chunk_metadata"]["table_id"] == "t1"
        assert get_chunk_store().get_by_table_ids("other", ["t1"]) == []
    finally:
        read_store_override.reset(token)


@pytest.mark.parametrize(
    "status,fixed,total,expected",
    [
        ("VALID", True, 5, "PASS"),
        ("VALID", False, 5, "FAIL"),
        ("VALID", True, 1, "FAIL"),
        ("INVALID_INFRA", True, 5, "INVALID"),
    ],
)
def test_managed_release_gate(status, fixed, total, expected):
    from backend.evaluation.release_gate import compute_module_release_gate

    report = SimpleNamespace(
        metadata={
            "managed_dataset": True,
            "corpus_snapshot_enforced": fixed,
            "corpus_source_mapping_complete": True,
            "run_validity": {"validity": status},
        },
        results=[SimpleNamespace(status="pass")] * total,
    )
    assert compute_module_release_gate(report)["verdict"] == expected


def test_assertions_reject_unknown_operation():
    from backend.evaluation.dataset.assertions import check_assertions

    assert (
        check_assertions(
            "三天",
            [
                {"type": "contains", "value": "三"},
                {"type": "not_contains", "value": "五"},
                {"type": "equals", "value": "三天"},
            ],
        )
        == [True] * 3
    )
    with pytest.raises(ValueError):
        check_assertions("三天", [{"type": "python", "value": "print(1)"}])


def test_synthetic_only_accepts_unique_original_quote():
    from backend.evaluation.dataset.synthetic import generated_payload

    sample = SimpleNamespace(
        eval_sample=SimpleNamespace(reference_contexts=["原文"], user_input="问题", reference="答案")
    )
    document = SimpleNamespace(page_content="这是原文", metadata={"doc_id": "d1"})
    payload = generated_payload(package()["manifest"], [sample], [document])
    assert payload["cases"][0]["expected"]["evidence"][0]["doc_id"] == "d1"
    assert imported(payload)["review"]["decision"] == "pending_review"
    with pytest.raises(ValueError, match="唯一映射"):
        generated_payload(
            package()["manifest"],
            [sample],
            [document, SimpleNamespace(page_content="这是原文", metadata={"doc_id": "other"})],
        )


def test_synthetic_rejects_reference_number_missing_from_evidence():
    from backend.evaluation.dataset.synthetic import generated_payload

    sample = SimpleNamespace(
        eval_sample=SimpleNamespace(
            reference_contexts=["年假需提前三个工作日提交申请。"],
            user_input="年假需要提前多久申请？",
            reference="年假需提前五个工作日提交申请。",
        )
    )
    document = SimpleNamespace(
        page_content="公司规定：年假需提前三个工作日提交申请。",
        metadata={"doc_id": "leave-policy"},
    )

    with pytest.raises(ValueError, match="原文未支持的数值"):
        generated_payload(package()["manifest"], [sample], [document])


def test_synthetic_accepts_chinese_number_matching_arabic_evidence():
    from backend.evaluation.dataset.synthetic import generated_payload

    sample = SimpleNamespace(
        eval_sample=SimpleNamespace(
            reference_contexts=["年假需提前3个工作日提交申请。"],
            user_input="年假需要提前多久申请？",
            reference="年假需提前三个工作日提交申请。",
        )
    )
    document = SimpleNamespace(
        page_content="公司规定：年假需提前3个工作日提交申请。",
        metadata={"doc_id": "leave-policy"},
    )

    payload = generated_payload(package()["manifest"], [sample], [document])

    assert payload["cases"][0]["expected"]["reference_answer"] == (
        "年假需提前三个工作日提交申请。"
    )


def test_synthetic_normalizes_arabic_large_unit_before_support_check():
    from backend.evaluation.dataset.synthetic import generated_payload

    sample = SimpleNamespace(
        eval_sample=SimpleNamespace(
            reference_contexts=["该项目预算为25000元。"],
            user_input="项目预算是多少？",
            reference="该项目预算为2.5万元。",
        )
    )
    document = SimpleNamespace(
        page_content="经审批，该项目预算为25000元。",
        metadata={"doc_id": "project-budget"},
    )

    payload = generated_payload(package()["manifest"], [sample], [document])

    assert payload["cases"][0]["expected"]["reference_answer"] == (
        "该项目预算为2.5万元。"
    )


def test_managed_worker_authorization_and_cancel_boundary(monkeypatch):
    from backend.evaluation import managed_tasks
    from backend.models.task import TaskRecord
    from backend.orchestration.checkpoint import TaskCancelled
    from backend.security import task_authorization
    from backend.services import task_service
    from backend.tasks import task_manager

    record = TaskRecord(id="task", user_id="1", graph_name="rag_eval", input={"binding_id": "b" * 24})
    heartbeat = SimpleNamespace(raise_if_lost=lambda: None)
    monkeypatch.setattr(
        task_authorization,
        "resolve_task_authorization",
        lambda *_: SimpleNamespace(principal=SimpleNamespace(roles=("viewer",))),
    )
    with pytest.raises(PermissionError):
        managed_tasks.execute(record, execution_id="lease", heartbeat=heartbeat)
    monkeypatch.setattr(
        task_authorization,
        "resolve_task_authorization",
        lambda *_: SimpleNamespace(principal=SimpleNamespace(roles=("super_admin",))),
    )
    monkeypatch.setattr(task_manager, "is_cancel_requested", lambda *_: True)
    monkeypatch.setattr(task_service, "update_status", lambda *_args, **_kwargs: pytest.fail("取消边界之后不可写进度"))
    with pytest.raises(TaskCancelled):
        managed_tasks.execute(record, execution_id="lease", heartbeat=heartbeat)


def test_eval_workflow_routes_to_existing_report_worker():
    from backend.tasks.queue_router import resolve_for_workflow

    assert resolve_for_workflow("rag_eval").workload_class == "report"


def test_snapshot_content_tampering_fails_closed():
    from backend.evaluation.corpus_snapshots import load

    identity = "a" * 24
    assets.write_once("snapshots", identity, {"snapshot_id": identity, "content_hash": "bad"})
    with pytest.raises(ValueError, match="校验失败"):
        load(identity)


def test_frozen_lifecycle_visibility_does_not_mix_current_registry(monkeypatch):
    from backend.rag.retrieval.review_filter import (
        get_review_blocked_doc_ids,
        verify_visible_doc_ids,
        visibility_snapshot,
    )

    token = visibility_snapshot.set(frozenset({"old-active"}))
    try:
        assert get_review_blocked_doc_ids() == frozenset()
        assert verify_visible_doc_ids(["old-active", "new-live-only"]) == {"old-active"}
    finally:
        visibility_snapshot.reset(token)


def test_legacy_source_mapping_cannot_pass_release_gate():
    from backend.evaluation.release_gate import compute_module_release_gate

    report = SimpleNamespace(
        metadata={
            "managed_dataset": True,
            "corpus_snapshot_enforced": True,
            "corpus_source_mapping_complete": False,
            "run_validity": {"validity": "VALID"},
        },
        results=[SimpleNamespace(status="pass")] * 5,
    )
    gate = compute_module_release_gate(report)
    assert gate["verdict"] == "FAIL"
    assert next(item for item in gate["items"] if item["metric"] == "source_mapping")["passed"] is False


@pytest.mark.parametrize(
    "role,actor", [("viewer", "user:2"), ("admin", "user:3"), ("super_admin", "service:internal-token")]
)
def test_managed_report_rejects_less_privileged_readers(monkeypatch, role, actor):
    import asyncio
    from fastapi import HTTPException
    from backend.app.api.deps import OperatorIdentity
    from backend.app.api.routes import evaluation

    monkeypatch.setattr(evaluation, "load_report", lambda _: (SimpleNamespace(metadata={"managed_dataset": True}), {}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            evaluation.get_eval_run("probe", _tenant="default", _operator=OperatorIdentity(role=role, actor=actor))
        )
    assert exc.value.status_code == 403


def test_idempotent_replay_does_not_redispatch_or_clear_cancel(monkeypatch):
    from backend.evaluation.managed_tasks import submit_payload
    from backend.models.task import TaskRecord
    from backend.services import task_service
    from backend.shared import idempotency
    from backend.tasks import task_manager

    record = TaskRecord(id="same-task", user_id="1", graph_name="rag_eval")
    monkeypatch.setattr(task_service, "ensure_schema", lambda: None)
    monkeypatch.setattr(task_service, "get_task", lambda _: record)
    monkeypatch.setattr(idempotency, "execute_idempotent_in_transaction", lambda *args: {"task_id": record.id, "run_id": record.id})
    monkeypatch.setattr(task_manager, "dispatch_task", lambda *_args, **_kwargs: pytest.fail("重放不得重新派发或清除取消标志"))
    assert submit_payload({"binding_id": "b" * 24}, "user:1", "request_123")["task_id"] == record.id


def test_workers_and_api_share_eval_reports_mount():
    from pathlib import Path
    import yaml

    compose = yaml.safe_load((Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(encoding="utf-8"))
    for name in ("app", "report-worker", "rag-index-worker"):
        assert "./data/eval_runs:/app/data/eval_runs" in compose["services"][name]["volumes"]


def test_worker_prompt_snapshot_uses_worker_event_loop(monkeypatch):
    import asyncio
    from backend.tasks.celery_app import refresh_worker_prompt_snapshot
    from backend.infra import async_utils
    from backend.prompts.service import prompt_service

    called = []
    async def refresh():
        called.append("refreshed")

    monkeypatch.setattr(prompt_service, "refresh_snapshot", refresh)
    monkeypatch.setattr(async_utils, "run_on_worker_loop", lambda coroutine: asyncio.run(coroutine))
    assert refresh_worker_prompt_snapshot() is True
    assert called == ["refreshed"]


def test_rag_evaluation_worker_has_sufficient_memory_and_ragas_dependency():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[3]
    for filename in ('docker-compose.local.yml', 'docker-compose.4c8g.yml'):
        compose = yaml.safe_load((root / '部署' / 'prod-4c8g' / filename).read_text(encoding='utf-8'))
        assert compose['services']['report-worker']['deploy']['resources']['limits']['memory'] == '768m'
    config = (root / 'pyproject.toml').read_text(encoding='utf-8')
    assert '"langchain-community>=0.3,<0.4.2"' in config
    assert '"rapidfuzz>=3.0,<4"' in config


def test_evaluation_task_distinguishes_automatic_retry_from_terminal_failure():
    from backend.evaluation.managed_tasks import task_view
    from backend.models.task import TaskRecord, TaskStatus

    record = TaskRecord(id='retry-probe', user_id='1', graph_name='rag_eval', status=TaskStatus.FAILED,
                        error_type='provider_error', retry_count=1, max_retries=3)
    assert task_view(record)['status'] == 'RETRYING'
    assert task_view(record)['raw_status'] == 'FAILED'
    record.retry_exhausted = True
    assert task_view(record)['status'] == 'FAILED'
    record.retry_exhausted = False
    record.error_type = 'permission_denied'
    assert task_view(record)['status'] == 'FAILED'


def test_synthesis_uses_visible_chunks_directly_and_keeps_request_budget(monkeypatch, tmp_path):
    import asyncio
    from contextlib import nullcontext
    from types import SimpleNamespace
    from ragas.testset.synthesizers.single_hop.specific import SingleHopSpecificQuerySynthesizer
    from backend.evaluation import corpus_snapshots, managed_execution, ragas_bridge
    from backend.evaluation.dataset import synthetic
    from backend.infra import async_utils
    from backend.infra.llm import budget, registry_store
    from backend.observability.tracer import trace_collector
    from backend.evaluation_v2 import repository
    from backend.rag.indexing import doc_registry

    monkeypatch.setenv('RAG_DATA_DIR', str(tmp_path))
    rows = {'documents': ['隐藏原文' * 100, '可见原文甲' * 100, '重复甲' * 100, '可见原文乙' * 100],
            'metadatas': [{'doc_id': value} for value in ('hidden', 'doc-a', 'doc-a', 'doc-b')]}
    monkeypatch.setattr(corpus_snapshots, 'load', lambda _: {'kb_id': 'kb', 'snapshot_id': 'a' * 24, 'visible_ids': ['doc-a', 'doc-b']})
    monkeypatch.setattr(corpus_snapshots, 'pipeline', lambda _: SimpleNamespace(vectordb=SimpleNamespace(get=lambda: rows)))
    monkeypatch.setattr(managed_execution, 'validate_kb', lambda _: {})
    class Registry:
        def list_by_kb(self, kb_id):
            assert kb_id == 'kb'
            return [
                {'doc_id': 'doc-a', 'version_id': 'version-a', 'status': 'active'},
                {'doc_id': 'doc-b', 'version_id': 'version-b', 'status': 'active'},
            ]
    monkeypatch.setattr(doc_registry, 'DocumentRegistry', lambda: Registry())
    stored_v2 = []
    monkeypatch.setattr(repository, 'ensure_dataset', lambda *_args, **_kwargs: {'dataset_id': 'v2-dataset'})
    def store_candidates(_tenant, _actor, *, source_kind, payloads, request_id):
        stored_v2.extend(payloads)
        assert source_kind == 'document_synthesized'
        assert request_id == 'task-probe'
        return [{'candidate_id': str(index)} for index, _ in enumerate(payloads)]
    monkeypatch.setattr(repository, 'create_candidates_bulk', store_candidates)
    async def refresh():
        pass
    monkeypatch.setattr(registry_store, 'refresh_registry', refresh)
    monkeypatch.setattr(async_utils, 'run_on_worker_loop', lambda coroutine: asyncio.run(coroutine))
    monkeypatch.setattr(ragas_bridge, '_init_configured_llm', lambda: SimpleNamespace(set_run_config=lambda _: None))
    budget_ids, calls, boundaries, finishes = [], [], [], []
    def scope(identity, **kwargs):
        budget_ids.append((identity, kwargs))
        return nullcontext()
    monkeypatch.setattr(budget, 'scoped_request_budget', scope)
    monkeypatch.setattr(trace_collector, 'start', lambda **_: object())
    monkeypatch.setattr(trace_collector, 'start_span', lambda *args, **kwargs: object())
    monkeypatch.setattr(trace_collector, 'end_span', lambda *args, **kwargs: None)
    monkeypatch.setattr(trace_collector, 'finish', lambda *args, **kwargs: finishes.append(kwargs))
    async def sample(self, scenario):
        from ragas.testset.synthesizers.single_hop.base import SingleHopScenario
        assert isinstance(scenario, SingleHopScenario)
        content = scenario.nodes[0].properties['page_content']
        calls.append(content)
        return SimpleNamespace(user_input=f'业务问题{len(calls)}', reference='可信答案', reference_contexts=[content])
    monkeypatch.setattr(SingleHopSpecificQuerySynthesizer, 'generate_sample', sample)
    request = {'snapshot_id': 'a' * 24, 'task_id': 'task-probe', 'tenant_id': 'default', 'actor': 'user:1', 'count': 5,
               'manifest': {'dataset_key': 'synthetic_probe', 'name': '候选题', 'version': '1.0.0'}}
    result = synthetic.generate(request, boundary=boundaries.append)
    assert result['review'] == 'pending_review' and result['case_count'] == 5
    assert result['v2_candidate_count'] == 5 and len(stored_v2) == 5
    assert 'expected' not in stored_v2[0] and stored_v2[0]['observed_answer'] == ''
    assert stored_v2[0]['proposed_evidence'][0]['doc_version'] in {'version-a', 'version-b'}
    assert budget_ids == [('eval-synthesis:task-probe', {'user_id': '1', 'tenant_id': 'default'})]
    assert len(calls) == 5 and all('隐藏' not in text and '重复' not in text for text in calls)
    assert len(boundaries) == 7 and len(finishes) == 1
    with pytest.raises(ValueError, match='1–5'):
        synthetic.generate({**request, 'count': 6}, boundary=boundaries.append)


@pytest.mark.parametrize('dispatch_type', ['initial', 'resume', 'recovery', 'admin_retry'])
def test_dispatch_preserves_cancel_and_pause_flags(monkeypatch, dispatch_type):
    from backend.models.task import TaskRecord
    from backend.tasks import task_manager, agent_tasks
    from backend.services import task_service
    from types import SimpleNamespace

    record = TaskRecord(id='control-probe', user_id='1', graph_name='rag_eval')
    monkeypatch.setattr(task_manager, 'clear_flags', lambda _: pytest.fail('派发不得清除已经送达的控制意图'))
    sent = []
    monkeypatch.setattr(agent_tasks.execute_agent_task, 'apply_async', lambda **kwargs: sent.append(kwargs) or SimpleNamespace(id='celery-probe'))
    monkeypatch.setattr(task_service, 'mark_queued', lambda *args, **kwargs: None)
    assert task_manager.enqueue_task(record, dispatch_type=dispatch_type) == 'celery-probe'
    assert sent == [{'args': ['control-probe'], 'queue': 'report'}]
