import asyncio
import csv
import io
import json
import uuid

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.app.api.routes.evaluation_imports import synthesize_dataset
from backend.app.api.routes.evaluation_v2 import (
    CandidateCreate,
    DatasetPublish,
    EvalRunCreate,
    EvaluatorCreate,
    EvidenceAnchorClaim,
    GateApprovalCreate,
    HumanReviewCreate,
    SuiteCreate,
    _build_run_evaluator_snapshot,
    _safe_error,
    create_eval_gate_approval,
    create_eval_human_review,
    create_eval_run,
    create_evaluator_version,
    export_dataset_version,
    get_dataset_version_coverage,
    list_candidates,
    list_suites,
    preview_dataset_import,
    publish_dataset_version,
    verify_evidence_anchors,
)
from backend.evaluation.corpus_snapshots import EmptyCorpusError
from backend.shared.error_protocol import ErrorCode, error_envelope_from_exception


def test_list_suites_includes_module_for_v2_run_selector(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "list_suites", lambda *_args, **_kwargs: [{
        "suite_id": "f05a3c09-9f55-4f5c-b925-90147e25a980",
        "suite_version_id": "750d3bd3-4fdd-4867-8a11-3dca7df97a60",
        "suite_key": "browser_suite",
        "name": "浏览器验收方案",
        "module": "rag",
        "suite_version": "1.0.0",
        "dataset_version": "1.0.1",
        "dataset_id": "d9376721-282f-47b2-81e8-f8e7c2a8a581",
        "dataset_manifest": {"items": [{"case_id": "8f6d7348-353c-4a64-8c80-3ad69798d60d"}]},
        "case_selection": {},
        "metric_policy": {"min_pass_rate": 0.95},
        "evaluator_versions": [],
        "security_policy": {},
    }])

    result = asyncio.run(list_suites(
        limit=50,
        offset=0,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        _actor=OperatorIdentity(role="admin", actor="user:alice"),
    ))

    item = result["items"][0]
    assert item["source"] == "evaluation_v2"
    assert item["status"] == "published"
    assert item["module"] == "rag"
    assert item["suite_version_id"] == "750d3bd3-4fdd-4867-8a11-3dca7df97a60"


def test_empty_corpus_returns_actionable_safe_api_error():
    with pytest.raises(HTTPException) as captured:
        _safe_error(EmptyCorpusError("内部验证文本不向客户端透传"))

    assert captured.value.status_code == 422
    envelope = error_envelope_from_exception(captured.value, source="http")
    assert envelope.code is ErrorCode.INVALID_PARAM
    assert envelope.message == "该知识库没有已发布文档，请先完成文档发布和索引后再运行评测。"


def test_import_preview_validates_without_creating_candidates(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "rag")
    payload = {
        "manifest": {
            "schema_version": "rag-eval/1.0",
            "dataset_key": "v2_import",
            "name": "V2 导入预览",
            "version": "1.0.0",
            "module": "rag",
        },
        "cases": [{
            "id": "CASE-001",
            "question": "年假如何申请？",
            "tier": "core",
            "query_type": "single_doc",
            "expected": {
                "behavior": "answer",
                "evidence": [{"doc_id": "doc-1", "quote": "提前三个工作日提交申请。"}],
                "reference_answer": "提前三个工作日提交。",
                "required_facts": ["提前三个工作日"],
            },
        }],
    }
    upload = UploadFile(filename="cases.json", file=io.BytesIO(json.dumps(payload).encode("utf-8")))
    result = asyncio.run(preview_dataset_import(
        file=upload,
        manifest="{}",
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="editor", actor="user:alice"),
    ))

    assert result["valid"] is True
    assert result["persisted"] is False
    assert result["case_count"] == 1
    assert "proposed_facts" in result["preview"][0]["candidate"]


def test_csv_preview_reports_five_missing_fields_in_one_hundred_rows_without_persisting(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "rag")

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["id", "question", "tier", "query_type", "expected"])
    writer.writeheader()
    expected = json.dumps({
        "behavior": "answer",
        "evidence": [{"doc_id": "doc-1", "quote": "制度原文"}],
        "reference_answer": "制度原文",
    }, ensure_ascii=False)
    for index in range(1, 101):
        writer.writerow({
            "id": f"CASE-{index:03d}",
            "question": "" if index > 95 else f"问题 {index}",
            "tier": "core",
            "query_type": "single_doc",
            "expected": expected,
        })
    upload = UploadFile(filename="cases.csv", file=io.BytesIO(output.getvalue().encode("utf-8")))
    manifest = json.dumps({
        "schema_version": "rag-eval/1.0",
        "dataset_key": "csv_preview",
        "name": "100 行导入预检",
        "version": "1.0.0",
        "module": "rag",
    })

    result = asyncio.run(routes.preview_dataset_import(
        file=upload,
        manifest=manifest,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="editor", actor="user:alice"),
    ))

    assert result["valid"] is False
    assert result["case_count"] == 100
    assert len(result["errors"]) == 5
    assert all(error["field"] == "question" for error in result["errors"])
    assert len(result["preview"]) == 5
    assert result["persisted"] is False


def test_non_rag_import_preview_uses_domain_contract_and_does_not_create_candidates(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "sql")
    case = {
        "id": "SQL-NEG-001",
        "question": "讀取未授權的系統表",
        "expected": {"should_reject": True, "expected_status": ["no_table"]},
    }
    upload = UploadFile(
        filename="sql-cases.jsonl",
        file=io.BytesIO((json.dumps(case, ensure_ascii=False) + "\n").encode("utf-8")),
    )
    manifest = json.dumps({
        "schema_version": "evaluation-v2/1.0",
        "dataset_key": "sql_golden",
        "name": "SQL 安全样本",
        "version": "1.0.0",
        "module": "sql",
    })

    result = asyncio.run(routes.preview_dataset_import(
        file=upload,
        manifest=manifest,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="editor", actor="user:alice"),
    ))

    assert result["valid"] is True
    assert result["persisted"] is False
    assert result["preview"][0]["candidate"]["proposed_facts"] == [case["expected"]]


def test_import_preview_rejects_module_dataset_mismatch(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "rag")
    case = {
        "id": "SQL-NEG-001",
        "question": "读取受限表",
        "expected": {"should_reject": True},
    }
    upload = UploadFile(
        filename="sql-cases.jsonl",
        file=io.BytesIO((json.dumps(case) + "\n").encode("utf-8")),
    )
    manifest = json.dumps({
        "schema_version": "evaluation-v2/1.0",
        "dataset_key": "rag_golden",
        "name": "错配导入",
        "version": "1.0.0",
        "module": "sql",
    })

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.preview_dataset_import(
            file=upload,
            manifest=manifest,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="editor", actor="user:alice"),
        ))
    assert exc.value.status_code == 422
    assert exc.value.detail == "导入模块与目标数据集业务域不一致"


def test_non_rag_review_keeps_sql_contract_and_skips_kb_evidence(monkeypatch):
    from starlette.requests import Request

    import backend.app.api.routes.evaluation_v2 as routes

    captured = {}

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(
        routes.repository,
        "get_candidate_review_context",
        lambda *_args: {
            "domain": "sql",
            "payload": {"input": {"query": "读取受限系统表"}},
        },
    )
    monkeypatch.setattr(
        routes.repository,
        "review_candidate",
        lambda *args, **kwargs: captured.update({"args": args, "kwargs": kwargs}) or {"review_status": "approved"},
    )
    monkeypatch.setattr(
        routes,
        "verify_evidence_anchors",
        lambda *_args: pytest.fail("SQL 样本不应调用 RAG 证据核验"),
    )
    body = routes.CandidateReview.model_validate({
        "expected_revision": 1,
        "verdict": "approved",
        "rationale": "已核对拒绝状态与安全预期。",
        "expected": {"should_reject": True, "expected_status": ["no_table"]},
        "evidence": [],
    })

    result = asyncio.run(routes.review_candidate(
        candidate_id=uuid.UUID("42000000-0000-4000-8000-000000000001"),
        body=body,
        request=Request({"type": "http", "headers": []}),
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="admin", actor="user:alice"),
    ))

    assert result["review_status"] == "approved"
    assert captured["kwargs"]["expected"] == body.expected
    assert captured["kwargs"]["evidence"] == []


def test_candidate_queue_is_hidden_from_viewers():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(list_candidates(
            review_status=None,
            limit=50,
            offset=0,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="viewer", actor="user:alice"),
        ))

    assert exc.value.status_code == 403


def test_candidate_payload_rejects_direct_expected_value():
    with pytest.raises(ValidationError):
        CandidateCreate.model_validate({
            "source_kind": "manual",
            "idempotency_key": "request-123",
            "payload": {
                "input": {"query": "问题"},
                "expected": {"answer": "不能直接成为 Golden"},
            },
        })


def test_suite_requires_a_published_evaluator_reference_and_supported_module():
    payload = {
        "suite_key": "support_baseline",
        "name": "客服基线",
        "module": "rag",
        "dataset_version_id": "d610a498-d547-4675-836e-78b78f7b0bb3",
        "version": "1.0.0",
        "minimum_valid_samples": 1,
    }
    with pytest.raises(ValidationError):
        SuiteCreate.model_validate(payload)

    payload["evaluator_versions"] = [{
        "evaluator_version_id": "f1010f93-6d87-4988-85fc-17408a53bbcf",
        "binding": "answer_quality",
    }]
    assert SuiteCreate.model_validate(payload).evaluator_versions[0].binding == "answer_quality"

    offline_quality = {**payload, "module": "travel-provider"}
    assert SuiteCreate.model_validate(offline_quality).module == "travel-provider"

    unsupported = {**payload, "module": "planner"}
    with pytest.raises(ValidationError):
        SuiteCreate.model_validate(unsupported)


def test_repository_rejects_unsupported_suite_module_before_database(monkeypatch):
    from backend.evaluation_v2 import repository

    def fail_if_database_is_touched(_tenant_id):
        raise AssertionError("不支持的模块不应访问数据库")

    monkeypatch.setattr(repository, "_transaction", fail_if_database_is_touched)
    with pytest.raises(ValueError, match="尚未适配"):
        repository.create_suite(
            "tenant-a",
            "user:alice",
            suite_key="customer_service",
            name="客服评测",
            module="customer_service",
            dataset_version_id="00000000-0000-0000-0000-000000000001",
            version="1.0.0",
            case_selection={},
            evaluator_versions=[],
            metric_policy={},
            security_policy={},
            minimum_valid_samples=1,
        )


def test_repository_rejects_suite_dataset_domain_mismatch_before_writing(monkeypatch):
    from contextlib import contextmanager

    from backend.evaluation_v2 import repository

    class Cursor:
        statement = ""

        def execute(self, statement, _params=None):
            self.statement = " ".join(statement.split())

        def fetchone(self):
            assert "JOIN evaluation_v2.eval_datasets" in self.statement
            return {
                "dataset_version_id": "00000000-0000-0000-0000-000000000001",
                "manifest_json": {},
                "content_hash": "dataset-hash",
                "domain": "sql",
            }

    cursor = Cursor()

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    monkeypatch.setattr(repository, "_validate_evaluator_refs", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="与 Golden 业务域 sql 不一致"):
        repository.create_suite(
            "tenant-a",
            "user:alice",
            suite_key="rag_baseline",
            name="RAG 基线",
            module="rag",
            dataset_version_id="00000000-0000-0000-0000-000000000001",
            version="1.0.0",
            case_selection={},
            evaluator_versions=[],
            metric_policy={},
            security_policy={},
            minimum_valid_samples=1,
        )

    assert "INSERT INTO evaluation_v2.eval_suites" not in cursor.statement


def test_evaluator_creation_is_admin_only():
    body = EvaluatorCreate.model_validate({
        "evaluator_key": "answer_quality",
        "version": "1.0.0",
        "evaluator_kind": "deterministic",
        "config": {"threshold": 0.9},
    })
    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_evaluator_version(
            body=body,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="editor", actor="user:alice"),
        ))

    assert exc.value.status_code == 403


def test_gate_approval_requires_super_admin_and_passes_bound_decision(monkeypatch):
    run_id = uuid.uuid4()
    gate_decision_id = uuid.uuid4()
    body = GateApprovalCreate.model_validate({
        "gate_decision_id": str(gate_decision_id),
        "request_id": "approval-1234",
        "decision": "approved",
        "release_reference": "release:v2.4.1",
        "rationale": "已核验回归、安全样本与发布范围。",
    })
    captured = {}

    def record(tenant_id, actor, selected_run, **kwargs):
        captured.update({"tenant_id": tenant_id, "actor": actor, "run_id": selected_run, **kwargs})
        return {"approval_id": str(uuid.uuid4()), "decision": kwargs["decision"]}

    monkeypatch.setattr("backend.app.api.routes.evaluation_v2.repository.record_gate_approval", record)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_eval_gate_approval(
            run_id=run_id,
            body=body,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="admin", actor="user:alice"),
        ))
    assert exc.value.status_code == 403

    result = asyncio.run(create_eval_gate_approval(
        run_id=run_id,
        body=body,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="super_admin", actor="user:alice"),
    ))
    assert result["decision"] == "approved"
    assert captured == {
        "tenant_id": "tenant-a",
        "actor": "user:alice",
        "run_id": str(run_id),
        "gate_decision_id": str(gate_decision_id),
        "request_id": "approval-1234",
        "decision": "approved",
        "release_reference": "release:v2.4.1",
        "rationale": "已核验回归、安全样本与发布范围。",
    }


def test_gate_approval_rejects_blank_rationale_and_invalid_reference():
    valid = {
        "gate_decision_id": str(uuid.uuid4()),
        "request_id": "approval-1234",
        "decision": "approved",
        "release_reference": "release:v2.4.1",
        "rationale": "        ",
    }
    with pytest.raises(ValidationError):
        GateApprovalCreate.model_validate(valid)
    valid["rationale"] = "满足审批要求。"
    valid["release_reference"] = "release tag with spaces"
    with pytest.raises(ValidationError):
        GateApprovalCreate.model_validate(valid)


def test_human_review_requires_super_admin_and_uses_rubric_scores(monkeypatch):
    run_id = uuid.uuid4()
    case_id = uuid.uuid4()
    evaluator_id = uuid.uuid4()
    body = HumanReviewCreate.model_validate({
        "evaluator_version_id": str(evaluator_id),
        "request_id": "human-review-1234",
        "label": "partially_correct",
        "scores": {"accuracy": 0.7, "evidence": 0.8},
        "rationale": "主要事实正确，但还需要补充一处证据。",
    })
    captured = {}

    def record(tenant_id, actor, selected_run, selected_case, **kwargs):
        captured.update({"tenant_id": tenant_id, "actor": actor, "run_id": selected_run, "case_id": selected_case, **kwargs})
        return {"review_id": str(uuid.uuid4()), "label": kwargs["label"], "overall_score": 0.75}

    monkeypatch.setattr("backend.app.api.routes.evaluation_v2.repository.record_human_review", record)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_eval_human_review(
            run_id=run_id,
            case_id=case_id,
            body=body,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="admin", actor="user:alice"),
        ))
    assert exc.value.status_code == 403

    result = asyncio.run(create_eval_human_review(
        run_id=run_id,
        case_id=case_id,
        body=body,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="super_admin", actor="user:alice"),
    ))
    assert result["label"] == "partially_correct"
    assert captured == {
        "tenant_id": "tenant-a",
        "actor": "user:alice",
        "run_id": str(run_id),
        "case_id": str(case_id),
        "evaluator_version_id": str(evaluator_id),
        "request_id": "human-review-1234",
        "label": "partially_correct",
        "scores": {"accuracy": 0.7, "evidence": 0.8},
        "rationale": "主要事实正确，但还需要补充一处证据。",
    }


def test_human_rubric_and_review_payload_reject_invalid_scores():
    with pytest.raises(ValidationError):
        EvaluatorCreate.model_validate({
            "evaluator_key": "human_quality",
            "version": "1.0.0",
            "evaluator_kind": "human",
            "config": {"criteria": [{"key": "accuracy", "label": "准确", "weight": 0.7}]},
        })
    with pytest.raises(ValidationError):
        HumanReviewCreate.model_validate({
            "evaluator_version_id": str(uuid.uuid4()),
            "request_id": "human-review-1234",
            "label": "correct",
            "scores": {"accuracy": 1.1},
            "rationale": "复核理由足够长。",
        })


def test_run_snapshot_keeps_human_rubric_separate_from_machine_runner():
    versions = [
        {"evaluator_version_id": "machine-v1", "binding": "answer_quality"},
        {"evaluator_version_id": "human-v1", "binding": "human_review"},
    ]
    definitions = [
        {
            "evaluator_version_id": "machine-v1",
            "evaluator_key": "answer_quality",
            "version": "1.0.0",
            "evaluator_kind": "deterministic",
            "rubric_hash": "a" * 64,
            "binding": "answer_quality",
            "config_json": {"adapter": "legacy-rag-answer-status-v1"},
        },
        {
            "evaluator_version_id": "human-v1",
            "evaluator_key": "human_quality",
            "version": "2.0.0",
            "evaluator_kind": "human",
            "rubric_hash": "b" * 64,
            "binding": "human_review",
            "config_json": {"criteria": [
                {"key": "accuracy", "label": "事实准确", "weight": 0.6},
                {"key": "evidence", "label": "证据充分", "weight": 0.4},
            ]},
        },
    ]

    snapshot = _build_run_evaluator_snapshot(versions, definitions)

    assert snapshot[0]["adapter"] == "legacy-rag-answer-status-v1"
    assert snapshot[1]["adapter"] == "manual-review-v1"
    assert snapshot[1]["config"]["criteria"][1]["weight"] == 0.4
    assert "config" not in snapshot[0]

    with pytest.raises(ValueError, match="至少需要一个机器评分器"):
        _build_run_evaluator_snapshot([versions[1]], [definitions[1]])


def test_non_rag_run_queues_without_a_corpus_snapshot(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes
    from backend.evaluation import managed_tasks

    suite_version_id = uuid.uuid4()
    evaluator_version_id = uuid.uuid4()
    case_id = uuid.uuid4()
    definition = {
        "module": "travel-provider",
        "suite_hash": "suite-hash",
        "dataset_hash": "dataset-hash",
        "dataset_version_id": str(uuid.uuid4()),
        "dataset_version": "1.0.0",
        "minimum_valid_samples": 1,
        "cases": [{
            "case_id": str(case_id),
            "case_revision": 1,
            "revision_hash": "case-hash",
            "metadata_json": {"probe": "place_success"},
        }],
        "evaluator_versions": [{
            "evaluator_version_id": str(evaluator_version_id),
            "binding": "answer_quality",
        }],
        "evaluator_definitions": [{
            "evaluator_version_id": str(evaluator_version_id),
            "evaluator_key": "answer_quality",
            "version": "1.0.0",
            "evaluator_kind": "deterministic",
            "rubric_hash": "a" * 64,
            "binding": "answer_quality",
            "config_json": {},
        }],
    }
    captured = {}
    monkeypatch.setattr(routes.repository, "get_run_definition", lambda *_: definition)
    monkeypatch.setattr(routes.repository, "get_run_by_request", lambda *_: None)
    monkeypatch.setattr(routes.repository, "create_run", lambda *_args, **kwargs: captured.update(kwargs) or {"run_id": kwargs["run_id"]})
    monkeypatch.setattr(routes.repository, "get_run", lambda _tenant, run_id: {"run_id": run_id, "job_status": "queued"})
    monkeypatch.setattr(managed_tasks, "submit_v2_run", lambda run_id, _actor: {"run_id": run_id, "status": "PENDING"})

    result = asyncio.run(create_eval_run(
        body=EvalRunCreate(
            suite_version_id=suite_version_id,
            request_id="nonrag-run-123",
        ),
        identity=Identity(user_id="1", tenant_id="default"),
        principal=None,
        actor=OperatorIdentity(role="super_admin", actor="user:1"),
    ))

    assert result["run"]["run_id"] == result["task"]["run_id"]
    assert captured["legacy_snapshot"] is None
    assert captured["kb_id"] is None
    assert captured["permissions_digest"] is None
    assert "corpus_snapshot_id" not in captured["snapshot_json"]
    assert captured["snapshot_json"]["execution_semantics"] == "system_quality"
    assert captured["snapshot_json"]["evaluator_definitions"][0]["adapter"] == "legacy-travel-provider-result-status-v1"


def test_evidence_anchor_is_derived_from_authorized_current_source(monkeypatch):
    from types import SimpleNamespace

    from backend.rag.authz import RagAuthorization

    monkeypatch.setattr(
        RagAuthorization,
        "build",
        staticmethod(lambda _principal: SimpleNamespace(kb_scope=frozenset({"kb_policy"}))),
    )

    class Registry:
        def list_by_kb(self, kb_id):
            assert kb_id == "kb_policy"
            return [{
                "doc_id": "doc-1",
                "version_id": "v2",
                "file_hash": "a" * 64,
                "status": "active",
            }]

    class ChunkStore:
        def get_by_doc_id(self, doc_id):
            assert doc_id == "doc-1"
            return [{
                "kb_id": "kb_policy",
                "doc_id": "doc-1",
                "chunk_id": "chunk-7",
                "content": "制度规定：提前三个工作日提交申请。",
            }]

    verified = verify_evidence_anchors(
        [{
            "kb_id": "kb_policy",
            "doc_id": "doc-1",
            "doc_version": "v2",
            "quote": "提前三个工作日提交申请。",
        }],
        "user:alice",
        object(),
        registry=Registry(),
        chunk_store=ChunkStore(),
    )

    assert verified[0]["anchor_id"] == "chunk-7"
    assert verified[0]["source_content_hash"] == "a" * 64
    assert verified[0]["verified_by"] == "user:alice"

    with pytest.raises(PermissionError):
        verify_evidence_anchors(
            [{"kb_id": "kb_other", "doc_id": "doc-1", "doc_version": "v2", "quote": "制度规定"}],
            "user:alice",
            object(),
            registry=Registry(),
            chunk_store=ChunkStore(),
        )


def test_evidence_anchor_id_disambiguates_overlapping_chunks(monkeypatch):
    from types import SimpleNamespace

    from backend.rag.authz import RagAuthorization

    monkeypatch.setattr(
        RagAuthorization,
        "build",
        staticmethod(lambda _principal: SimpleNamespace(kb_scope=frozenset({"kb_policy"}))),
    )

    class Registry:
        def list_by_kb(self, _kb_id):
            return [{
                "doc_id": "doc-1",
                "version_id": "v2",
                "file_hash": "a" * 64,
                "status": "active",
            }]

    class ChunkStore:
        def get_by_doc_id(self, _doc_id):
            return [
                {
                    "kb_id": "kb_policy",
                    "doc_id": "doc-1",
                    "chunk_id": "question-answer",
                    "content": "问题：差旅报销需要哪些附件？\n答案：提前三个工作日提交申请。",
                },
                {
                    "kb_id": "kb_policy",
                    "doc_id": "doc-1",
                    "chunk_id": "answer-only",
                    "content": "提前三个工作日提交申请。",
                },
            ]

    claims = [{
        "kb_id": "kb_policy",
        "doc_id": "doc-1",
        "doc_version": "v2",
        "anchor_id": "answer-only",
        "quote": "提前三个工作日提交申请。",
    }]
    verified = verify_evidence_anchors(
        claims,
        "user:alice",
        object(),
        registry=Registry(),
        chunk_store=ChunkStore(),
    )
    assert verified[0]["anchor_id"] == "answer-only"

    claims[0]["quote"] = "错误的证据引用"
    with pytest.raises(ValueError, match="不属于指定原文锚点"):
        verify_evidence_anchors(
            claims,
            "user:alice",
            object(),
            registry=Registry(),
            chunk_store=ChunkStore(),
        )


def test_evidence_claim_rejects_client_supplied_verification_hashes():
    with pytest.raises(ValidationError):
        EvidenceAnchorClaim.model_validate({
            "kb_id": "kb_policy",
            "doc_id": "doc-1",
            "doc_version": "v2",
            "quote": "原文片段",
            "anchor_text_hash": "a" * 64,
            "verified_by": "user:alice",
        })


def test_dataset_publish_revalidates_stored_evidence_before_persisting(monkeypatch):
    from starlette.requests import Request

    import backend.app.api.routes.evaluation_v2 as routes

    case_id = "32000000-0000-4000-8000-000000000001"
    dataset_id = "32000000-0000-4000-8000-000000000002"
    evidence = [{
        "kb_id": "kb_policy",
        "doc_id": "doc-1",
        "doc_version": "v2",
        "anchor_id": "chunk-7",
        "anchor_text_hash": "a" * 64,
        "source_content_hash": "b" * 64,
        "verified_by": "user:alice",
        "verified_at": "2026-10-24T00:00:00+00:00",
    }]
    calls = []

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "rag")
    monkeypatch.setattr(
        routes.repository,
        "list_case_evidence",
        lambda tenant_id, selected: [{"evidence_json": evidence}],
    )
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_principal",
        lambda request: "trusted-principal",
    )

    def verify(claims, actor, principal):
        calls.append(("verify", claims, actor, principal))

    def publish(*args, **kwargs):
        calls.append(("publish", args, kwargs))
        return {"version": "1.0.0"}

    monkeypatch.setattr(routes, "verify_evidence_anchors", verify)
    monkeypatch.setattr(routes.repository, "publish_dataset_version", publish)
    request = Request({"type": "http", "headers": []})

    result = asyncio.run(publish_dataset_version(
        dataset_id=uuid.UUID(dataset_id),
        body=DatasetPublish.model_validate({
            "version": "1.0.0",
            "items": [{"case_id": case_id, "revision": 1}],
        }),
        request=request,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="admin", actor="user:alice"),
    ))

    assert result == {"version": "1.0.0"}
    assert [call[0] for call in calls] == ["verify", "publish"]
    assert calls[0][1:] == (evidence, "user:alice", "trusted-principal")

    calls.clear()

    def reject_stale(*_args):
        raise ValueError("证据来源文档已变化")

    monkeypatch.setattr(routes, "verify_evidence_anchors", reject_stale)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(publish_dataset_version(
            dataset_id=uuid.UUID(dataset_id),
            body=DatasetPublish.model_validate({
                "version": "1.0.0",
                "items": [{"case_id": case_id, "revision": 1}],
            }),
            request=request,
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="admin", actor="user:alice"),
        ))
    assert exc.value.status_code == 422
    assert calls == []


def test_dataset_publish_accepts_valid_refusal_without_evidence(monkeypatch):
    from starlette.requests import Request

    import backend.app.api.routes.evaluation_v2 as routes

    case_id = "32000000-0000-4000-8000-000000000001"
    dataset_id = "32000000-0000-4000-8000-000000000002"
    calls = []

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "rag")
    monkeypatch.setattr(
        routes.repository,
        "list_case_evidence",
        lambda tenant_id, selected: [{
            "expected_json": {"behavior": "refuse", "reason": "no_evidence"},
            "evidence_json": [],
        }],
    )
    monkeypatch.setattr(
        routes,
        "verify_evidence_anchors",
        lambda *_args: pytest.fail("没有证据的拒答样本不应初始化 KB 核验"),
    )
    monkeypatch.setattr(
        routes.repository,
        "publish_dataset_version",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"version": "1.0.0"},
    )

    result = asyncio.run(publish_dataset_version(
        dataset_id=uuid.UUID(dataset_id),
        body=DatasetPublish.model_validate({
            "version": "1.0.0",
            "items": [{"case_id": case_id, "revision": 1}],
        }),
        request=Request({"type": "http", "headers": []}),
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="admin", actor="user:alice"),
    ))

    assert result == {"version": "1.0.0"}
    assert calls == [(("tenant-a", "user:alice", dataset_id), {
        "version": "1.0.0",
        "case_ids": [(case_id, 1)],
    })]


def test_legacy_ragas_generator_rejects_non_default_trusted_tenant():
    from types import SimpleNamespace

    with pytest.raises(HTTPException) as exc:
        asyncio.run(synthesize_dataset(
            body=SimpleNamespace(),
            identity=Identity(user_id="alice", tenant_id="tenant-b"),
            operator=OperatorIdentity(role="super_admin", actor="user:alice"),
        ))

    assert exc.value.status_code == 403


def test_golden_export_revalidates_evidence_and_records_audit(monkeypatch):
    from datetime import datetime, timezone

    from fastapi import Request

    import backend.app.api.routes.evaluation_v2 as routes

    dataset_id = uuid.UUID("e1a582c4-d95a-4535-aa02-3fe89de75e61")
    version_id = uuid.UUID("e64b72fb-a279-4b5a-abce-c87cc3b602dd")
    case_id = "acbb92cf-b12c-46d9-9855-219b8083604f"
    evidence = [{"kb_id": "kb_policy", "doc_id": "doc-1", "quote": "已核验引用"}]
    exported = {
        "dataset_id": str(dataset_id),
        "dataset_key": "support_golden",
        "dataset_name": "客服评测集",
        "domain": "rag",
        "dataset_version_id": str(version_id),
        "version": "1.0.0",
        "content_hash": "a" * 64,
        "published_by": "user:alice",
        "published_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
        "cases": [{
            "case_id": case_id,
            "revision": 2,
            "input": {"query": "请假如何申请？"},
            "expected": {"behavior": "answer", "reference_answer": "提前申请"},
            "evidence": evidence,
            "metadata": {"scenario": "policy"},
            "content_hash": "b" * 64,
        }],
    }
    calls = []

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(
        routes.repository,
        "load_dataset_version_export",
        lambda tenant_id, current_dataset_id, current_version_id: exported,
    )
    monkeypatch.setattr(
        routes.repository,
        "record_dataset_version_export",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_principal",
        lambda request: "trusted-principal",
    )
    monkeypatch.setattr(
        routes,
        "verify_evidence_anchors",
        lambda claims, actor, principal: calls.append((claims, actor, principal)),
    )

    response = asyncio.run(export_dataset_version(
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        request=Request({"type": "http", "headers": []}),
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        actor=OperatorIdentity(role="admin", actor="user:alice"),
    ))

    async def read_body():
        return b"".join([chunk async for chunk in response.body_iterator])

    body = asyncio.run(read_body()).decode("utf-8").splitlines()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["content-disposition"] == 'attachment; filename="support_golden-1.0.0.jsonl"'
    assert json.loads(body[0])["content_hash"] == "a" * 64
    assert json.loads(body[1])["case_id"] == case_id
    assert calls[0] == (evidence, "user:alice", "trusted-principal")
    assert calls[1][0] == ("tenant-a", "user:alice", str(version_id))
    assert calls[1][1] == {"content_hash_value": "a" * 64, "case_count": 1}


def test_golden_export_rejects_stale_evidence_before_audit(monkeypatch):
    from datetime import datetime, timezone

    from fastapi import Request

    import backend.app.api.routes.evaluation_v2 as routes

    dataset_id = uuid.UUID("e1a582c4-d95a-4535-aa02-3fe89de75e61")
    version_id = uuid.UUID("e64b72fb-a279-4b5a-abce-c87cc3b602dd")
    exported = {
        "dataset_id": str(dataset_id),
        "dataset_key": "support_golden",
        "dataset_name": "客服评测集",
        "domain": "rag",
        "dataset_version_id": str(version_id),
        "version": "1.0.0",
        "content_hash": "a" * 64,
        "published_by": "user:alice",
        "published_at": datetime.now(timezone.utc),
        "cases": [{"evidence": [{"kb_id": "kb_policy"}]}],
    }
    audited = []

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(routes.repository, "load_dataset_version_export", lambda *args: exported)
    monkeypatch.setattr(
        routes.repository,
        "record_dataset_version_export",
        lambda *args, **kwargs: audited.append(True),
    )
    monkeypatch.setattr(
        "backend.app.api.identity.resolve_principal",
        lambda request: "trusted-principal",
    )

    def stale_evidence(*args):
        raise ValueError("证据失效")

    monkeypatch.setattr(routes, "verify_evidence_anchors", stale_evidence)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(export_dataset_version(
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            request=Request({"type": "http", "headers": []}),
            identity=Identity(user_id="alice", tenant_id="tenant-a"),
            actor=OperatorIdentity(role="admin", actor="user:alice"),
        ))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "EVIDENCE_STALE"
    assert audited == []


def test_dataset_coverage_returns_only_redacted_dimension_buckets(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    dataset_id = uuid.UUID("e1a582c4-d95a-4535-aa02-3fe89de75e61")
    version_id = uuid.UUID("e64b72fb-a279-4b5a-abce-c87cc3b602dd")
    snapshot = {
        "dataset_id": str(dataset_id),
        "dataset_version_id": str(version_id),
        "version": "1.0.0",
        "content_hash": "a" * 64,
        "case_count": 3,
        "cases": [
            {"category": "policy", "scenario": "leave", "risk_level": "low", "difficulty": "easy"},
            {"category": "policy", "scenario": "leave", "risk_level": "high", "difficulty": "medium"},
            {"category": "alice@example.com", "scenario": "call 13812345678", "risk_level": "medium", "difficulty": None},
        ],
    }

    async def direct_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(routes.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(routes.repository, "get_dataset_version_coverage", lambda *args: snapshot)

    result = asyncio.run(get_dataset_version_coverage(
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        identity=Identity(user_id="alice", tenant_id="tenant-a"),
        _actor=OperatorIdentity(role="viewer", actor="user:alice"),
    ))

    categories = result["dimensions"]["category"]
    assert result["case_count"] == 3
    assert categories["covered_count"] == 3
    assert categories["items"][0]["label"] == "policy"
    assert categories["items"][0]["count"] == 2
    assert "alice@example.com" not in repr(result)
    assert "13812345678" not in repr(result)
    assert "cases" not in result

