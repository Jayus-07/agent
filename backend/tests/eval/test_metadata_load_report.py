"""metadata-load-v1 证据报告的指标口径测试。"""
from __future__ import annotations

from backend.eval.metadata_baseline.load_report import build_load_report


def test_build_load_report_records_two_x_peak_and_token_saving() -> None:
    report = build_load_report(
        baseline_peak_concurrency=4,
        observed_peak_concurrency=8,
        queue_wait_ms=[0.0, 4.0, 8.0, 12.0, 16.0],
        processing_ms=[20.0, 30.0, 40.0, 50.0, 60.0],
        lineage_api_ms=[5.0, 7.0, 9.0, 11.0, 13.0],
        db_pool_wait_ms=[0.0, 1.0, 2.0, 3.0, 4.0],
        duration_seconds=10.0,
        submitted_count=40,
        completed_count=40,
        failed_count=0,
        embedding_calls=0,
        llm_calls=0,
        llm_429_count=0,
        cache_hits=80,
        cache_misses=0,
        duplicate_write_count=0,
        queue_drained=True,
        shadow_enabled=False,
    )

    assert report["report_version"] == "metadata-load-v1"
    assert report["peak_multiplier"] == 2.0
    assert report["queue_age_p95_seconds"] == 0.016
    assert report["primary_p95_ms"] == 60.0
    assert report["lineage_api_p95_ms"] == 13.0
    assert report["db_pool_wait_p95_ms"] == 4.0
    assert report["cache_hit_rate"] == 1.0
    assert report["model_call_count"] == 0
    assert report["sustained_queue_growth"] is False
    assert report["shadow_enabled"] is False


def test_build_load_report_marks_failures_and_missing_samples() -> None:
    report = build_load_report(
        baseline_peak_concurrency=4,
        observed_peak_concurrency=4,
        queue_wait_ms=[],
        processing_ms=[],
        lineage_api_ms=[],
        db_pool_wait_ms=[],
        duration_seconds=0.0,
        submitted_count=3,
        completed_count=1,
        failed_count=2,
        embedding_calls=1,
        llm_calls=2,
        llm_429_count=1,
        cache_hits=1,
        cache_misses=2,
        duplicate_write_count=1,
        queue_drained=False,
        shadow_enabled=True,
        shadow_processing_ms=[30.0, 40.0],
    )

    assert report["peak_multiplier"] == 1.0
    assert report["queue_age_p95_seconds"] == 0.0
    assert report["primary_p95_ms"] == 0.0
    assert report["shadow_p95_ms"] == 40.0
    assert report["llm_429_rate"] == 0.5
    assert report["cache_hit_rate"] == 1 / 3
    assert report["duplicate_write_count"] == 1
    assert report["sustained_queue_growth"] is True
    assert report["failed_count"] == 2
