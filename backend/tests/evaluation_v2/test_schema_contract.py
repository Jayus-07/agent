from pathlib import Path

from scripts.init_db import MIGRATION_TARGETS, discover_migrations

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "migrations" / "108_evaluation_v2_domain.sql"
APPROVAL_MIGRATION = ROOT / "sql" / "migrations" / "109_evaluation_v2_gate_approvals.sql"
HUMAN_REVIEW_MIGRATION = ROOT / "sql" / "migrations" / "110_evaluation_v2_human_reviews.sql"
CASE_REVISION_MIGRATION = ROOT / "sql" / "migrations" / "111_evaluation_v2_case_revision_candidates.sql"
DOMAIN_RUNNER_MIGRATION = ROOT / "sql" / "migrations" / "112_evaluation_v2_domain_runner.sql"


def test_evaluation_v2_migration_is_registered_for_memory_database():
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    assert MIGRATION_TARGETS[MIGRATION.name] == "memory"
    assert MIGRATION_TARGETS[APPROVAL_MIGRATION.name] == "memory"
    assert MIGRATION_TARGETS[HUMAN_REVIEW_MIGRATION.name] == "memory"
    assert MIGRATION_TARGETS[CASE_REVISION_MIGRATION.name] == "memory"
    assert MIGRATION_TARGETS[DOMAIN_RUNNER_MIGRATION.name] == "memory"
    assert names.index("107_auth_knowledge_bases.sql") < names.index(MIGRATION.name)
    assert names.index(MIGRATION.name) < names.index(APPROVAL_MIGRATION.name)
    assert names.index(APPROVAL_MIGRATION.name) < names.index(HUMAN_REVIEW_MIGRATION.name)
    assert names.index(HUMAN_REVIEW_MIGRATION.name) < names.index(CASE_REVISION_MIGRATION.name)
    assert names.index(CASE_REVISION_MIGRATION.name) < names.index(DOMAIN_RUNNER_MIGRATION.name)


def test_non_rag_runs_do_not_require_a_corpus_snapshot():
    ddl = DOMAIN_RUNNER_MIGRATION.read_text(encoding="utf-8")

    assert "ALTER TABLE evaluation_v2.eval_runs" in ddl
    assert "ALTER COLUMN corpus_snapshot_id DROP NOT NULL" in ddl


def test_case_revision_candidate_schema_binds_target_and_base_revision():
    ddl = CASE_REVISION_MIGRATION.read_text(encoding="utf-8")

    assert "revision_target_case_id UUID" in ddl
    assert "revision_base_number INTEGER" in ddl
    assert "FOREIGN KEY (tenant_id, revision_target_case_id)" in ddl
    assert "revision_base_number > 0" in ddl


def test_evaluation_v2_schema_enforces_tenant_and_immutable_boundaries():
    ddl = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE SCHEMA IF NOT EXISTS evaluation_v2" in ddl
    assert "CREATE ROLE evaluation_v2_app NOLOGIN NOSUPERUSER NOBYPASSRLS" in ddl
    assert "SET LOCAL ROLE evaluation_v2_app" in (
        ROOT / "evaluation_v2" / "repository.py"
    ).read_text(encoding="utf-8")
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "current_setting('app.tenant_id', true)" in ddl
    assert "UNIQUE (tenant_id, request_id)" in ddl
    assert "UNIQUE (tenant_id, run_id, case_id)" in ddl
    assert "published evaluation versions are immutable" in ddl
    assert "job_status" in ddl and "gate_verdict" in ddl
    assert "source_kind <> 'trace' OR NOT (payload_json ? 'expected')" in ddl

    policy_block = ddl.split("FOREACH table_name IN ARRAY ARRAY[", 1)[1].split("] LOOP", 1)[0]
    tenant_tables = (
        "eval_datasets", "eval_cases", "eval_case_revisions", "eval_candidates",
        "eval_annotations", "eval_dataset_versions", "eval_dataset_items", "eval_suites",
        "eval_evaluator_versions", "eval_suite_versions", "eval_corpus_snapshots",
        "eval_generation_jobs", "eval_feedback_intake", "eval_runs", "eval_case_results",
        "eval_experiments", "eval_gate_decisions", "eval_audit_events",
    )
    assert all(f"'{table}'" in policy_block for table in tenant_tables)


def test_gate_approval_schema_is_tenant_scoped_and_append_only():
    ddl = APPROVAL_MIGRATION.read_text(encoding="utf-8")

    assert "UNIQUE (tenant_id, request_id)" in ddl
    assert "FOREIGN KEY (tenant_id, run_id)" in ddl
    assert "FOREIGN KEY (tenant_id, gate_decision_id)" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "current_tenant_id()" in ddl
    assert "BEFORE UPDATE OR DELETE" in ddl
    assert "reject_immutable_mutation()" in ddl
    assert "不触发 CI 或部署" in ddl


def test_human_review_schema_is_tenant_scoped_append_only_and_bound_to_result_and_rubric():
    ddl = HUMAN_REVIEW_MIGRATION.read_text(encoding="utf-8")

    assert "UNIQUE (tenant_id, request_id)" in ddl
    assert "FOREIGN KEY (tenant_id, run_id, case_id)" in ddl
    assert "FOREIGN KEY (tenant_id, evaluator_version_id)" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "current_tenant_id()" in ddl
    assert "BEFORE UPDATE OR DELETE" in ddl
    assert "reject_immutable_mutation()" in ddl
    assert "不直接改变机器评分或 Gate" in ddl
