"""RAG 维护任务的过期运行收口编排测试。"""

from backend.tasks import rag_maintenance_tasks


def test_reconcile_stale_rag_runs_reports_both_stores(monkeypatch):
    calls = []

    class _IndexRuns:
        def reconcile_stale_active_runs(self, **kwargs):
            calls.append(("index", kwargs))
            return ["upload-1", "upload-2"]

    class _ProcessingRuns:
        def fail_stale_running_runs(self, **kwargs):
            calls.append(("lineage", kwargs))
            return {"run_count": 3, "step_count": 4}

    monkeypatch.setattr(
        "backend.config.tasks.RAG_INDEX_RUN_STALE_SECONDS", 3660)
    monkeypatch.setattr(
        "backend.config.tasks.TASK_PENDING_RECOVERY_MAX_AGE_SECONDS", 86400)
    monkeypatch.setattr(
        "backend.rag.indexing.index_run_store_pg.get_index_run_store",
        lambda: _IndexRuns(),
    )
    monkeypatch.setattr(
        "backend.rag.indexing.processing_lineage_pg.get_processing_lineage_repository",
        lambda: _ProcessingRuns(),
    )

    result = rag_maintenance_tasks._reconcile_stale_rag_runs()

    assert result == {
        "threshold_seconds": 3660,
        "unpublished_threshold_seconds": 86400,
        "index_runs_failed": 2,
        "processing_runs_failed": 3,
        "processing_steps_failed": 4,
        "errors": [],
    }
    assert calls == [
        ("index", {
            "stale_after_seconds": 3660,
            "unpublished_after_seconds": 86400,
        }),
        ("lineage", {"stale_after_seconds": 3660}),
    ]


def test_reconcile_stale_rag_runs_keeps_partial_failure_visible(monkeypatch):
    class _BrokenIndexRuns:
        def reconcile_stale_active_runs(self, **_kwargs):
            raise RuntimeError("registry unavailable")

    class _ProcessingRuns:
        def fail_stale_running_runs(self, **_kwargs):
            return {"run_count": 1, "step_count": 0}

    monkeypatch.setattr(
        "backend.config.tasks.RAG_INDEX_RUN_STALE_SECONDS", 3660)
    monkeypatch.setattr(
        "backend.config.tasks.TASK_PENDING_RECOVERY_MAX_AGE_SECONDS", 86400)
    monkeypatch.setattr(
        "backend.rag.indexing.index_run_store_pg.get_index_run_store",
        lambda: _BrokenIndexRuns(),
    )
    monkeypatch.setattr(
        "backend.rag.indexing.processing_lineage_pg.get_processing_lineage_repository",
        lambda: _ProcessingRuns(),
    )

    result = rag_maintenance_tasks._reconcile_stale_rag_runs()

    assert result["index_runs_failed"] == 0
    assert result["unpublished_threshold_seconds"] == 86400
    assert result["processing_runs_failed"] == 1
    assert result["errors"] == ["index_runs: registry unavailable"]
