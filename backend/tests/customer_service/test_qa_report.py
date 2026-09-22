"""test_qa_report.py — 质检每日报表（批次D）单元测试。

覆盖：
  - 日期区间工具
  - generate_daily_report：成功路径（mock 采集+落库）与失败路径（ok=False）
  - run_daily_report 同步入口存在
  - celery 任务注册（薄壳 import 无副作用）
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from backend.customer_service import qa_report as qr


def test_day_range_utc_boundaries():
    d = date(2026, 9, 21)
    start, end = qr._day_range(d)
    assert start == datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_generate_success(monkeypatch):
    metrics = {
        "conversations": {"total": 10, "handoff_rate": 0.2},
        "satisfaction": {"avg_rating": 4.5, "rated_count": 8},
    }
    upserted = {}

    async def _fake_collect(report_date, tenant_id):
        return metrics

    async def _fake_upsert(report_date, tenant_id, m):
        upserted["args"] = (report_date, tenant_id, m)

    monkeypatch.setattr(qr, "_collect_metrics", _fake_collect)
    monkeypatch.setattr(qr, "_upsert_report", _fake_upsert)

    result = await qr.generate_daily_report(date(2026, 9, 21), "default")
    assert result["ok"] is True
    assert result["metrics"]["conversations"]["total"] == 10
    assert upserted["args"][0] == date(2026, 9, 21)
    assert upserted["args"][1] == "default"


@pytest.mark.asyncio
async def test_generate_failure_returns_ok_false(monkeypatch):
    """DB 挂了必须显式失败（ok=False），绝不静默吞掉。"""
    async def _boom(report_date, tenant_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(qr, "_collect_metrics", _boom)
    result = await qr.generate_daily_report(date(2026, 9, 21), "default")
    assert result["ok"] is False
    assert "db down" in result["error"]


def test_run_daily_report_entry_exists():
    assert callable(qr.run_daily_report)


def test_celery_task_registered():
    # 任务模块经 celery include 在 worker 启动时注册；测试先显式 import
    import backend.tasks.cs_qa_tasks  # noqa: F401
    from backend.tasks.celery_app import celery_app

    assert "cs.qa_daily_report" in celery_app.tasks


def test_beat_schedule_daily():
    from backend.tasks.celery_app import celery_app

    entry = celery_app.conf.beat_schedule.get("cs-qa-daily-report")
    assert entry is not None
    assert entry["task"] == "cs.qa_daily_report"


def test_prometheus_recorder_tolerant():
    """指标记录失败不影响报表（容错）。"""
    qr._record_prometheus_safe({"conversations": {}, "satisfaction": {}})
    qr._record_prometheus_safe({})  # 空指标也不炸
