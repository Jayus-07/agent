import pytest
from pydantic import ValidationError

from backend.app.api.routes.evaluation_v2 import CandidatePayload
from backend.evaluation_v2 import repository
from backend.evaluation_v2.capabilities import require_supported_runner_evaluators
from backend.evaluation_v2.privacy import sanitize_candidate_payload
from backend.evaluation_v2.repository import (
    RepositoryNotFoundError,
    _normalize_suite_selection,
    _tenant,
    _validate_evaluator_refs,
    content_hash,
    normalize_human_rubric,
)


def test_candidate_contract_keeps_trace_answer_out_of_expected():
    with pytest.raises(ValidationError):
        CandidatePayload.model_validate({
            "input": {"query": "问题"},
            "observed_answer": "线上答案",
            "expected": {"reference_answer": "不能直接成为金标"},
        })


def test_candidate_privacy_redacts_phone_email_and_secret_fields():
    clean, changed = sanitize_candidate_payload({
        "input": {"query": "联系 13812345678 或 alice@example.com"},
        "metadata": {"api_key": "secret-value"},
        "observed_answer": "Bearer abc.def.ghi",
    })

    assert changed is True
    assert "13812345678" not in clean["input"]["query"]
    assert "alice@example.com" not in clean["input"]["query"]
    assert clean["metadata"]["api_key"] == "[REDACTED]"
    assert "abc.def.ghi" not in clean["observed_answer"]


def test_privacy_filter_does_not_confuse_token_count_with_a_secret():
    clean, changed = sanitize_candidate_payload({"max_output_tokens": 2048})

    assert clean == {"max_output_tokens": 2048}
    assert changed is False


def test_tenant_scope_fails_closed_and_hash_is_canonical():
    with pytest.raises(PermissionError):
        _tenant("")
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})


def test_run_environment_details_must_match_immutable_hash():
    environment = {"runner": "legacy-rag-v1", "runtime_environment": {"rag_mode": "remote"}}
    env_hash = content_hash(environment)

    repository._validate_run_environment({"environment": environment}, env_hash)
    with pytest.raises(ValueError, match="运行环境快照与环境哈希不一致"):
        repository._validate_run_environment(
            {"environment": {**environment, "runner": "changed"}}, env_hash
        )


def test_local_model_identity_does_not_expose_machine_path():
    from backend.evaluation_v2.runtime_environment import (
        _local_model_identity,
        _safe_identifier,
    )

    identity = _local_model_identity(r"C:\models\bge-small")

    assert identity["model"] == "bge-small"
    assert identity["path_fingerprint"]
    assert "C:" not in str(identity)
    assert "\\models\\" not in str(identity)
    assert _safe_identifier("sk-private-secret-value") != "sk-private-secret-value"
    assert _safe_identifier("https://host.invalid/path?api_key=secret") != (
        "https://host.invalid/path?api_key=secret"
    )


def test_suite_evaluator_refs_must_be_published_in_the_same_tenant():
    evaluator_id = "1b2b1703-f972-4f99-b77a-f14d8a67ea8a"

    class Cursor:
        def __init__(self, rows):
            self.rows = rows
            self.query = ""
            self.params = None

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchall(self):
            return self.rows

    cursor = Cursor([{"evaluator_version_id": evaluator_id, "evaluator_kind": "deterministic"}])
    _validate_evaluator_refs(
        cursor,
        "tenant-a",
        [{"evaluator_version_id": evaluator_id, "binding": "answer_quality"}],
    )
    assert "tenant_id = %s" in cursor.query
    assert "status = 'published'" in cursor.query
    assert "FOR SHARE" in cursor.query
    assert "evaluator_kind" in cursor.query
    assert cursor.params == ("tenant-a", [evaluator_id])

    with pytest.raises(RepositoryNotFoundError):
        _validate_evaluator_refs(
            Cursor([]),
            "tenant-a",
            [{"evaluator_version_id": evaluator_id, "binding": "answer_quality"}],
        )


def test_suite_evaluator_bindings_reject_unadapted_judge_and_type_mismatch():
    from backend.evaluation_v2.capabilities import UnsupportedRunnerCapabilityError

    evaluator_id = "1b2b1703-f972-4f99-b77a-f14d8a67ea8a"
    refs = [{"evaluator_version_id": evaluator_id, "binding": "answer_quality"}]

    with pytest.raises(UnsupportedRunnerCapabilityError, match="要求 deterministic"):
        require_supported_runner_evaluators(refs, {evaluator_id: "llm_judge"})

    with pytest.raises(UnsupportedRunnerCapabilityError):
        require_supported_runner_evaluators(
            [{"evaluator_version_id": evaluator_id, "binding": "human_review"}],
            {evaluator_id: "human"},
        )


def test_suite_binding_must_be_supported_by_runner():
    from backend.evaluation_v2.capabilities import UnsupportedRunnerCapabilityError

    evaluator_id = "1b2b1703-f972-4f99-b77a-f14d8a67ea8a"
    with pytest.raises(UnsupportedRunnerCapabilityError) as exc:
        require_supported_runner_evaluators(
            [{"evaluator_version_id": evaluator_id, "binding": "custom_judge"}],
            {evaluator_id: "deterministic"},
        )
    assert "custom_judge" not in str(exc.value)


def test_unsupported_runner_capability_uses_safe_actionable_error():
    from fastapi import HTTPException

    from backend.app.api.routes.evaluation_v2 import _safe_error
    from backend.evaluation_v2.capabilities import UnsupportedRunnerCapabilityError

    with pytest.raises(HTTPException) as exc:
        _safe_error(UnsupportedRunnerCapabilityError(
            "answer_quality 绑定当前要求 deterministic 评分器；所选版本未适配。"
        ))

    assert exc.value.status_code == 422
    assert exc.value.user_message == "answer_quality 绑定当前要求 deterministic 评分器；所选版本未适配。"


def test_publish_existing_suite_draft_rechecks_evaluator_capability(monkeypatch):
    from contextlib import contextmanager

    evaluator_id = "1b2b1703-f972-4f99-b77a-f14d8a67ea8a"
    suite_version_id = "8b2b1703-f972-4f99-b77a-f14d8a67ea8b"

    class Cursor:
        def __init__(self):
            self.statement = ""
            self.statements = []

        def execute(self, statement, _params=None):
            self.statement = " ".join(statement.split())
            self.statements.append(self.statement)

        def fetchone(self):
            if "SELECT s.module" in self.statement:
                return {
                    "module": "rag",
                    "domain": "rag",
                    "evaluator_versions": [{
                        "evaluator_version_id": evaluator_id,
                        "binding": "answer_quality",
                    }],
                }
            raise AssertionError("不兼容评分器必须在发布 UPDATE 之前被拒绝")

        def fetchall(self):
            return [{"evaluator_version_id": evaluator_id, "evaluator_kind": "llm_judge"}]

    cursor = Cursor()

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    monkeypatch.setattr(repository, "_transaction", transaction)
    with pytest.raises(ValueError, match="要求 deterministic"):
        repository.publish_suite_version("tenant-a", "user:admin", suite_version_id)

    assert len(cursor.statements) == 2
    assert all("UPDATE evaluation_v2.eval_suite_versions" not in query for query in cursor.statements)


def test_suite_selection_only_accepts_unique_golden_case_uuids():
    case_id = "11111111-1111-4111-8111-111111111111"
    assert _normalize_suite_selection({"case_ids": [case_id]}) == (
        {"case_ids": [case_id]},
        [case_id],
    )
    with pytest.raises(ValueError):
        _normalize_suite_selection({"case_ids": [case_id, case_id]})
    with pytest.raises(ValueError):
        _normalize_suite_selection({"tier": "hard"})


def test_human_rubric_requires_unique_dimensions_and_unit_weight_sum():
    rubric = normalize_human_rubric({"criteria": [
        {"key": "accuracy", "label": "事实准确", "weight": 0.6},
        {"key": "evidence", "label": "证据充分", "weight": 0.4},
    ]})

    assert rubric["criteria"][0]["key"] == "accuracy"
    with pytest.raises(ValueError, match="权重之和必须为 1"):
        normalize_human_rubric({"criteria": [
            {"key": "accuracy", "label": "事实准确", "weight": 0.6},
            {"key": "evidence", "label": "证据充分", "weight": 0.3},
        ]})
