"""处理血缘 PostgreSQL 迁移和仓储契约测试。"""

from contextlib import contextmanager
from pathlib import Path
import threading

from backend.rag.indexing.processing_lineage import ProcessingRunContext
from backend.rag.indexing.processing_lineage_pg import (
    PostgresProcessingLineageRepository,
)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | list]] = []
        self.rowcount = 1
        self._rows: list[dict] = []

    def execute(self, sql, params=()):
        self.executed.append((str(sql), params))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.commits = 0

    def cursor(self, **kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _repo_with_connection(connection: _Connection):
    repo = PostgresProcessingLineageRepository.__new__(
        PostgresProcessingLineageRepository
    )
    repo._runs_table = "rag_processing_runs"
    repo._steps_table = "rag_processing_steps"
    repo._index_runs_table = "rag_index_runs"
    repo._lock = threading.Lock()

    @contextmanager
    def _conn():
        yield connection

    repo._conn = _conn
    return repo


def test_migration_defines_run_step_usage_and_registry_columns():
    migration = Path(__file__).parents[2] / "sql" / "migrations" / "027_rag_processing_lineage.sql"
    sql = migration.read_text(encoding="utf-8")

    for fragment in (
        "CREATE TABLE IF NOT EXISTS rag_processing_runs",
        "CREATE TABLE IF NOT EXISTS rag_processing_steps",
        "UNIQUE (run_id, stage, attempt_no)",
        "last_processing_run_id",
        "config_snapshot_hash",
        "run_id",
        "step_id",
    ):
        assert fragment in sql


def test_repository_upserts_stage_by_run_stage_and_attempt():
    connection = _Connection()
    repo = _repo_with_connection(connection)
    context = ProcessingRunContext.create("doc-1", "hash-1", "upload")
    connection.cursor_instance._rows = [{"run_id": context.run_id}]
    step_id = context.begin_stage(
        "embedding", role="embedding", engine_type="embedding", ordinal=7
    )
    context.finish_stage(step_id, status="success", input_count=2, output_count=2)

    repo.upsert_stage(context.snapshot().steps[0])

    sql, params = connection.cursor_instance.executed[-1]
    lock_sql, lock_params = connection.cursor_instance.executed[0]
    assert "INSERT INTO rag_processing_steps" in sql
    assert "ON CONFLICT (run_id, stage, attempt_no) DO UPDATE" in sql
    assert "status = 'running'" in lock_sql
    assert "FOR SHARE" in lock_sql
    assert lock_params == (context.run_id,)
    assert context.run_id in params
    assert step_id in params


def test_repository_finishes_run_without_replacing_stage_history():
    connection = _Connection()
    repo = _repo_with_connection(connection)

    repo.finish_run(
        "run-1",
        "success",
        finished_at="2026-09-20T00:00:00Z",
        model_summary=[{"role": "embedding", "model_name": "text-embedding-v3"}],
    )

    sql, params = connection.cursor_instance.executed[-1]
    assert "UPDATE rag_processing_runs" in sql
    assert "model_summary" in sql
    assert "AND status = 'running'" in sql
    assert params[0] == "success"
    assert params[-1] == "run-1"


def test_repository_fails_stale_runs_and_only_their_running_steps():
    connection = _Connection()
    connection.cursor_instance._rows = [{"run_count": 2, "step_count": 3}]
    repo = _repo_with_connection(connection)

    result = repo.fail_stale_running_runs(stale_after_seconds=3660, limit=600)

    assert result == {"run_count": 2, "step_count": 3}
    sql, params = connection.cursor_instance.executed[-1]
    assert "WITH candidates AS" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "run.status = 'running'" in sql
    assert "step.status = 'running'" in sql
    assert "step.run_id = finished_runs.run_id" in sql
    assert params == ("3660", 500, "运行超过配置的处理时限，系统已将未完成阶段收口为失败。",
                      "运行超过配置的处理时限，系统已将未完成阶段收口为失败。")


def test_repository_counts_duplicate_stage_keys_for_selected_runs():
    connection = _Connection()
    connection.cursor_instance._rows = [{"duplicate_count": 2}]
    repo = _repo_with_connection(connection)

    assert repo.count_duplicate_stage_keys(["run-1", "run-2"]) == 2
    sql, params = connection.cursor_instance.executed[-1]
    assert "GROUP BY run_id, stage, attempt_no" in sql
    assert params == (["run-1", "run-2"],)


def test_candidate_run_lookup_is_scoped_and_returns_latest_per_upload():
    connection = _Connection()
    connection.cursor_instance._rows = [
        {
            "run_id": "run-new",
            "task_id": "upload-1",
            "doc_id": "doc-1",
            "file_hash": "a" * 64,
            "trace_id": "trace-new",
            "kb_id": "kb-test",
            "tenant_id": "tenant-a",
            "department": "general",
            "permission_scope": "general",
        },
        {
            "run_id": "run-old",
            "task_id": "upload-1",
            "doc_id": "doc-1",
            "file_hash": "a" * 64,
            "trace_id": "trace-old",
            "kb_id": "kb-test",
            "tenant_id": "tenant-a",
            "department": "general",
            "permission_scope": "general",
        },
    ]
    repo = _repo_with_connection(connection)

    result = repo.list_candidate_runs_by_upload_ids(
        ["upload-1"], tenant_id="tenant-a", kb_ids=["kb-test"],
    )

    assert [item["run_id"] for item in result] == ["run-new"]
    sql, params = connection.cursor_instance.executed[-1]
    assert "JOIN rag_index_runs AS ir" in sql
    assert "ir.tenant_id = %s" in sql
    assert "ir.kb_id = ANY(%s)" in sql
    assert params == (["upload-1"], "tenant-a", ["kb-test"])


def test_candidate_run_scope_requires_matching_tenant_kb_doc_and_run():
    connection = _Connection()
    connection.cursor_instance._rows = [{
        "run_id": "run-1",
        "doc_id": "doc-1",
        "file_hash": "b" * 64,
        "trace_id": "trace-1",
        "kb_id": "kb-test",
        "tenant_id": "tenant-a",
        "department": "general",
        "permission_scope": "general",
    }]
    repo = _repo_with_connection(connection)

    result = repo.get_candidate_run_scope(
        "doc-1",
        "run-1",
        tenant_id="tenant-a",
        kb_ids=["kb-test"],
    )

    assert result["trace_id"] == "trace-1"
    sql, params = connection.cursor_instance.executed[-1]
    assert "pr.doc_id = %s" in sql
    assert "pr.run_id = %s" in sql
    assert "ir.tenant_id = %s" in sql
    assert "ir.kb_id = ANY(%s)" in sql
    assert params == ("doc-1", "run-1", "tenant-a", ["kb-test"])


def test_chunking_fallback_stats_are_scoped_to_processing_identity_snapshot():
    connection = _Connection()
    connection.cursor_instance._rows = [{
        "kb_audience": "internal",
        "candidate_count": 3,
        "llm_call_count": 2,
        "applied_count": 1,
        "provider_failure_count": 1,
        "boundary_rejection_count": 0,
        "not_applied_count": 1,
        "skipped_count": 1,
        "output_chunk_count": 8,
        "last_seen_at": "2026-11-15T00:00:00Z",
        "source_format": "pdf",
        "fallback_type": "rule_coverage_gap",
    }]
    repo = _repo_with_connection(connection)

    result = repo.chunking_fallback_stats(
        tenant_id="tenant-a",
        kb_ids=["kb-a"],
        user_permissions=["support"],
        is_admin=False,
        department="general",
        days=14,
        review_threshold=2,
    )

    assert result["window_days"] == 14
    assert result["review_threshold"] == 2
    assert result["count_unit"] == "processing_run"
    assert result["candidate_count"] == 3
    assert result["applied_count"] == 1
    assert result["provider_failure_count"] == 1
    assert result["by_audience"] == [{
        "kb_audience": "internal",
        "candidate_count": 3,
        "llm_call_count": 2,
        "applied_count": 1,
        "provider_failure_count": 1,
        "boundary_rejection_count": 0,
        "not_applied_count": 1,
        "skipped_count": 1,
        "output_chunk_count": 8,
    }]
    assert result["by_type"][0]["upgrade_review_required"] is True
    assert len(connection.cursor_instance.executed) == 3
    for sql, params in connection.cursor_instance.executed:
        assert "step.metadata->>'tenant_id' = %s" in sql
        assert "step.metadata->>'kb_id' = ANY(%s::text[])" in sql
        assert "unnest(string_to_array(" in sql
        assert "ANY(%s::text[])" in sql
        assert "FROM doc_registry" not in sql
        assert "step.metadata->>'failure_type' IS DISTINCT FROM 'context_budget_rejected'" in sql
        assert "step.metadata->>'failure_class' IS DISTINCT FROM 'ContextBudgetExceededError'" in sql
        assert "step.metadata->>'failure_type' = 'provider_or_runtime_error'" in sql
        assert "step.metadata->>'failure_class' IS DISTINCT FROM 'ContextBudgetExceededError'" in sql
        assert "'context_budget_rejected'" in sql
        assert "step.metadata->>'failure_class' = 'ContextBudgetExceededError'" in sql
        assert "AS boundary_rejection_count" in sql
        assert "step.metadata->>'department'" in sql
        has_audience_join = "LEFT JOIN auth.knowledge_bases AS kb" in sql
        assert has_audience_join == ("SELECT COUNT(*) AS candidate_count" not in sql)
        if has_audience_join:
            assert "COALESCE(kb.audience, 'unknown') AS kb_audience" in sql
        assert params == (
            14,
            "tenant-a",
            ["kb-a"],
            False,
            ["support"],
            "general",
        )


def test_chunking_fallback_stats_do_not_signal_production_upgrade_for_test_kb():
    connection = _Connection()
    connection.cursor_instance._rows = [{
        "kb_audience": "test",
        "candidate_count": 5,
        "llm_call_count": 5,
        "applied_count": 5,
        "provider_failure_count": 0,
        "boundary_rejection_count": 0,
        "not_applied_count": 0,
        "skipped_count": 0,
        "output_chunk_count": 40,
        "last_seen_at": "2026-11-15T00:00:00Z",
        "source_format": "txt",
        "fallback_type": "no_recognized_structure",
        "rule_strategy": "RecursiveChunkStrategy",
        "rule_version": "v2",
        "parser_version": "v1",
    }]
    repo = _repo_with_connection(connection)

    result = repo.chunking_fallback_stats(
        tenant_id="tenant-a",
        kb_ids=["test-kb"],
        user_permissions=["support"],
        is_admin=False,
        department="general",
        days=30,
        review_threshold=5,
    )

    assert result["by_type"][0]["kb_audience"] == "test"
    assert result["by_type"][0]["llm_call_count"] == 5
    assert result["by_type"][0]["upgrade_review_required"] is False
    assert result["by_audience"][0]["kb_audience"] == "test"


def test_repository_uses_one_bounded_lazy_connection_pool(monkeypatch):
    import backend.rag.indexing.processing_lineage_pg as lineage_pg

    created = []

    class _Pool:
        def __init__(self, min_conn, max_conn, **config):
            self.bounds = (min_conn, max_conn)
            self.config = config
            self.connection = _Connection()
            self.borrowed = 0
            self.returned = 0
            created.append(self)

        def getconn(self):
            self.borrowed += 1
            return self.connection

        def putconn(self, connection, close=False):
            assert connection is self.connection
            assert close is False
            self.returned += 1

    monkeypatch.setattr(lineage_pg, "ThreadedConnectionPool", _Pool)
    monkeypatch.setattr(lineage_pg, "DB_POOL_MIN_CONN", 2)
    monkeypatch.setattr(lineage_pg, "DB_POOL_MAX_CONN", 10)

    repository = PostgresProcessingLineageRepository()
    assert created == []

    with repository._conn() as connection:
        assert connection is created[0].connection
    with repository._conn() as connection:
        assert connection is created[0].connection

    assert len(created) == 1
    assert created[0].bounds == (2, 10)
    assert created[0].borrowed == 2
    assert created[0].returned == 2

    pool_stats = repository.pool_wait_stats()
    assert pool_stats["sample_count"] == 2
    assert pool_stats["p95_ms"] >= 0
