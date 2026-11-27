"""架构守卫测试：确保 rag_upload 保持单一职责与模块化架构，防止代码膨胀与契约漂移。"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import APIRouter


def test_facade_file_size_boundary():
    """门面守卫：backend/app/api/routes/rag_upload.py 必须是轻量门面（不超过 150 行）。"""
    facade_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "routes"
        / "rag_upload.py"
    )
    assert facade_path.exists(), f"Facade file not found: {facade_path}"

    line_count = len(facade_path.read_text(encoding="utf-8").splitlines())
    assert (
        line_count <= 150
    ), f"rag_upload.py 超过了 150 行门面限制 (当前: {line_count} 行)，请勿在门面堆砌实现逻辑！"


def test_subroutes_structure_complete():
    """子领域路由包结构守卫：必须包含全部 7 个专职领域模块。"""
    subroutes_dir = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "routes"
        / "rag_upload_subroutes"
    )
    assert subroutes_dir.is_dir(), f"Subroutes package not found: {subroutes_dir}"

    expected_files = {
        "__init__.py",
        "base.py",
        "lock.py",
        "validation.py",
        "cleanup.py",
        "progress.py",
        "indexing.py",
        "failures.py",
        "routes.py",
    }
    actual_files = {p.name for p in subroutes_dir.glob("*.py")}
    assert expected_files.issubset(
        actual_files
    ), f"缺少领域子模块: {expected_files - actual_files}"


def test_facade_symbols_re_exported():
    """兼容性守卫：门面必须完整重新导出全部核心函数、异常类与常量。"""
    from backend.app.api.routes import rag_upload

    expected_classes = [
        "FileLockedByOtherError",
        "UploadIdempotencyConflict",
    ]
    for cls_name in expected_classes:
        assert hasattr(
            rag_upload, cls_name
        ), f"rag_upload 门面缺少导出 Class: {cls_name}"

    expected_helpers = [
        "_cleanup_candidate_artifacts",
        "_validate_ocr_page_budget",
        "_lock_age_seconds",
        "_try_create_lock",
        "acquire_index_lock",
        "_pdf_has_text_layer",
        "sha256_of_file",
        "release_index_lock",
        "_start_lock_heartbeat",
        "cleanup_stale_upload_artifacts",
        "cleanup_expired_progress_queues",
        "progress_queue_gc_loop",
        "_validate_mime",
        "_magic_matches",
        "sync_upload_impl",
        "_finalize_upload_queue",
        "_cleanup_failed_upload_sync",
        "_cleanup_failed_upload",
        "_write_progress_redis",
        "_mark_registry_failed",
        "_get_index_semaphore",
        "_latest_upload_trace_id",
        "_settle_index_result",
        "_dispatch_index_to_celery",
        "_dispatch_index_with_idempotency",
        "_resolve_replayed_dispatch",
        "_run_index_background",
        "_remove_bak",
        "_reverify_publish_authorization",
        "_latest_processing_trace_for_doc",
        "_active_document_artifacts_complete",
        "_active_vector_ids_for_doc",
        "_do_index_sync",
        "_read_progress_redis",
        "_run_still_active",
        "_redis_poll_events",
        "_redis_poll_stream_response",
        "_progress_owner_of",
        "_ensure_progress_owner",
        "_quality_failure_summary",
        "_load_quality_failure_details",
    ]
    for helper_name in expected_helpers:
        assert hasattr(
            rag_upload, helper_name
        ), f"rag_upload 门面缺少导出 Helper: {helper_name}"

    assert hasattr(rag_upload, "LOCK_STALE_SECONDS"), "缺少 LOCK_STALE_SECONDS"
    assert hasattr(rag_upload, "_INDEX_CONCURRENCY_LIMIT"), "缺少 _INDEX_CONCURRENCY_LIMIT"
    assert hasattr(rag_upload, "router"), "缺少 router 实例"


def test_routes_mounted_and_path_parity():
    """路由挂载与路径一致性守卫：确保 router 包含 3 个核心端点。"""
    from backend.app.api.routes import rag_upload

    router_paths = {r.path for r in rag_upload.router.routes}
    assert "/upload" in router_paths
    assert "/upload-failures" in router_paths
    assert "/upload/{upload_id}/stream" in router_paths
    assert len(rag_upload.router.routes) >= 3


def test_facade_proxy_monkeypatch_penetration(monkeypatch):
    """动态穿透守卫：验证外部 monkeypatch rag_upload 内部函数能够动态穿透至领域子模块。"""
    from backend.app.api.routes import rag_upload
    from backend.app.api.routes.rag_upload_subroutes.base import facade

    called = False

    def fake_lock_age(*args, **kwargs):
        nonlocal called
        called = True
        return 999.0

    monkeypatch.setattr(rag_upload, "_lock_age_seconds", fake_lock_age)

    res = facade._lock_age_seconds("fake_path")
    assert res == 999.0
    assert called is True
