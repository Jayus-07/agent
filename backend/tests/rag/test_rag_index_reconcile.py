"""索引对账必须区分 chunk 向量、文档向量及在途发布。"""

from backend.rag.indexing.reconcile import reconcile_snapshot

import pytest


def test_repair_plan_uses_paths_and_protects_duplicate_inflight_ids():
    from backend.rag.indexing import reconcile

    rows = [
        {"file_path": "/real/a", "doc_id": "a", "status": "active"},
        {"file_path": "/test/a", "doc_id": "a", "status": "deleted"},
        {"file_path": "/real/b", "doc_id": "b", "status": "active"},
        {"file_path": "/test/b", "doc_id": "b", "status": "parsing"},
        {"file_path": "/real/c", "doc_id": "c", "status": "active"},
    ]
    assert reconcile.build_missing_index_repairs(rows, {"c"}, set()) == [
        {"file_path": "/real/a", "doc_id": "a", "status": "active"}
    ]


def test_repair_plan_is_deterministic_and_does_not_mutate_snapshot():
    from backend.rag.indexing import reconcile

    rows = [
        {"file_path": "/z", "doc_id": "z", "status": "active"},
        {"file_path": "/a", "doc_id": "a", "status": "active"},
    ]
    result = reconcile.build_missing_index_repairs(rows, set(), {"z"})
    result[0]["status"] = "failed"
    assert rows[1]["status"] == "active"
    assert reconcile.build_missing_index_repairs(rows, set(), set())[0]["file_path"] == "/a"


def test_missing_chunks_not_masked_by_document_vectors():
    report = reconcile_snapshot(
        [{"doc_id": "a", "status": "active", "last_indexed": "2026-10-03"}],
        set(), {"a"}, set(), set(),
    )
    assert report["issues"] == {
        "active_missing_vectors": ["a"],
        "orphan_vectors": [],
        "chunk_vector_mismatch": ["a"],
        "indexed_missing_vectors": ["a"],
    }
    assert report["active_coverage"] == 0.0


def test_inflight_registry_and_candidate_runs_are_protected():
    report = reconcile_snapshot(
        [{"doc_id": "upload", "status": "parsing"},
         {"doc_id": "review", "status": "pending_review"},
         {"doc_id": "publish", "status": "active", "last_indexed": "now"}],
        {"upload", "review", "orphan"}, {"upload", "review"},
        {"publish"}, set(),
    )
    assert report["issues"]["orphan_vectors"] == ["orphan"]
    assert report["issues"]["active_missing_vectors"] == []
    assert report["issues"]["indexed_missing_vectors"] == []
    assert report["deferred_doc_ids"] == ["publish", "review", "upload"]
    assert report["active_coverage"] is None


def test_three_way_and_bm25_mismatch_are_not_hidden():
    report = reconcile_snapshot(
        [{"doc_id": "a", "status": "active"}],
        {"a", "ghost"}, {"a", "chunk_only"}, set(), {"ghost"},
    )
    assert report["issues"]["chunk_vector_mismatch"] == ["chunk_only", "ghost"]
    assert report["bm25_missing_doc_ids"] == ["a"]
    assert report["bm25_orphan_doc_ids"] == ["ghost"]
    assert report["consistent"] is False


def test_unavailable_bm25_is_not_reported_as_consistent():
    report = reconcile_snapshot(
        [{"doc_id": "a", "status": "active"}], {"a"}, {"a"}, set(), None,
    )
    assert report["issues"] == {
        "active_missing_vectors": [], "orphan_vectors": [],
        "chunk_vector_mismatch": [], "indexed_missing_vectors": [],
    }
    assert report["bm25_status"] == "unavailable"
    assert report["consistent"] is False
    assert report["active_coverage"] == 1.0


def test_empty_registry_does_not_claim_full_coverage():
    report = reconcile_snapshot([], set(), set(), set(), set())
    assert report["active_coverage"] is None
    assert report["consistent"] is True


def test_deleted_and_failed_history_does_not_create_live_index_alerts():
    report = reconcile_snapshot(
        [{"doc_id": "deleted", "status": "deleted", "last_indexed": "old"},
         {"doc_id": "failed", "status": "failed", "last_indexed": "old"},
         {"doc_id": "review", "status": "pending_review", "last_indexed": "old"}],
        set(), set(), set(), set(),
    )
    assert report["issues"]["indexed_missing_vectors"] == []
    assert report["consistent"] is True


def test_reconcile_beat_routes_to_maintenance_and_is_included():
    from backend.tasks.celery_app import celery_app
    from backend.tasks.queue_router import resolve_for_celery_task

    route = resolve_for_celery_task("rag.index_reconcile")
    assert route.workload_class == "maintenance"
    entry = celery_app.conf.beat_schedule["rag-index-reconcile"]
    assert entry["task"] == "rag.index_reconcile"
    assert entry["options"]["queue"] == route.physical_queue
    assert "backend.tasks.rag_maintenance_tasks" in celery_app.conf.include
    assert entry["schedule"].hour == {2}


def test_parsing_watchdog_is_registered_every_15_minutes():
    from backend.tasks.celery_app import celery_app
    from backend.tasks.queue_router import resolve_for_celery_task

    route = resolve_for_celery_task("rag.parsing_timeout_watchdog")
    entry = celery_app.conf.beat_schedule["rag-parsing-timeout-watchdog"]
    assert route.workload_class == "maintenance"
    assert entry["task"] == "rag.parsing_timeout_watchdog"
    assert entry["options"]["queue"] == route.physical_queue
    assert entry["schedule"] == 900.0


def test_reconcile_migration_is_registered_for_memory_database():
    from pathlib import Path

    from scripts.init_db import MIGRATION_TARGETS

    name = "071_rag_reconcile_reports.sql"
    assert MIGRATION_TARGETS[name] == "memory"
    root = Path(__file__).resolve().parents[2]
    ddl = (root / "sql" / "migrations" / name).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS ai.rag_reconcile_reports" in ddl
    assert "TIMESTAMPTZ" in ddl


def test_evaluation_migrations_are_registered_for_memory_database():
    from scripts.init_db import MIGRATION_TARGETS

    assert MIGRATION_TARGETS["074_eval_run_lifecycle.sql"] == "memory"
    assert MIGRATION_TARGETS["075_eval_run_samples.sql"] == "memory"


def test_strict_bm25_snapshot_distinguishes_missing_corrupt_and_empty(tmp_path):
    import pickle

    from backend.rag.retrieval.bm25_store import BM25Store

    store = BM25Store(str(tmp_path))
    with pytest.raises(RuntimeError):
        store.load_docs_strict()
    (tmp_path / "index.pkl").write_bytes(b"broken")
    with pytest.raises(RuntimeError):
        store.load_docs_strict()
    (tmp_path / "index.pkl").write_bytes(pickle.dumps({"docs": []}))
    assert store.load_docs_strict() == []
    (tmp_path / "index.pkl.sha256").write_text("bad-checksum")
    with pytest.raises(RuntimeError):
        store.load_docs_strict()


def test_collection_uses_runtime_path_and_metrics_use_latest_sample(monkeypatch):
    from backend.config import database
    from backend.observability import metrics
    from backend.rag.indexing.reconcile import default_collection

    monkeypatch.setattr(database, "CHROMA_PATH", "d:/tmp/custom_chunks")
    assert default_collection() == "custom_chunks"
    assert metrics.agent_rag_reconcile_inconsistent._multiprocess_mode == "livemostrecent"
    assert metrics.agent_rag_reconcile_source_available._multiprocess_mode == "livemostrecent"


def test_bm25_snapshot_uses_stdlib_http_when_httpx_is_unavailable(monkeypatch):
    import json
    import sys
    import urllib.request

    from backend.rag.indexing import reconcile

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"doc_ids": ["doc-a", "doc-b", "doc-a"]}).encode()

    def _urlopen(request, timeout):
        assert request.full_url.endswith("/admin/index/reconcile-snapshot")
        assert timeout == 30
        return _Response()

    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr("backend.config.rag.RAG_SERVICE_URL", "http://rag-service:8090")
    monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "service-token")

    assert reconcile.fetch_bm25_snapshot() == {"doc-a", "doc-b"}


def test_multiprocess_metrics_recover_after_second_report(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from prometheus_client import CollectorRegistry, generate_latest, multiprocess

    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path),
           "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    script = (
        "from backend.observability.metrics import "
        "agent_rag_reconcile_inconsistent as issues, "
        "agent_rag_reconcile_source_available as available; "
        "import sys; issues.set(int(sys.argv[1])); "
        "available.set(int(sys.argv[2]))"
    )
    subprocess.run([sys.executable, "-c", script, "7", "0"],
                   env=env, check=True, capture_output=True, timeout=30)
    subprocess.run([sys.executable, "-c", script, "0", "1"],
                   env=env, check=True, capture_output=True, timeout=30)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(tmp_path))
    metrics = generate_latest(registry).decode()
    assert "agent_rag_reconcile_inconsistent 0.0" in metrics
    assert "agent_rag_reconcile_source_available 1.0" in metrics
