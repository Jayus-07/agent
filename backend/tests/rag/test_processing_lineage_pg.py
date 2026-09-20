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
    step_id = context.begin_stage(
        "embedding", role="embedding", engine_type="embedding", ordinal=7
    )
    context.finish_stage(step_id, status="success", input_count=2, output_count=2)

    repo.upsert_stage(context.snapshot().steps[0])

    sql, params = connection.cursor_instance.executed[-1]
    assert "INSERT INTO rag_processing_steps" in sql
    assert "ON CONFLICT (run_id, stage, attempt_no) DO UPDATE" in sql
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
    assert params[0] == "success"
    assert params[-1] == "run-1"


def test_repository_counts_duplicate_stage_keys_for_selected_runs():
    connection = _Connection()
    connection.cursor_instance._rows = [{"duplicate_count": 2}]
    repo = _repo_with_connection(connection)

    assert repo.count_duplicate_stage_keys(["run-1", "run-2"]) == 2
    sql, params = connection.cursor_instance.executed[-1]
    assert "GROUP BY run_id, stage, attempt_no" in sql
    assert params == (["run-1", "run-2"],)


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
