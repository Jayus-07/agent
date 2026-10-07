"""GET /evaluation/runs 数据源收口回归测试（Phase 2 / 验收 A1-A3）。

- 列表必须以 PG ai.eval_run_records 为权威（旧实现遍历文件且只取 rag
  summary，SQL 42/45 的 93.3% 被显示成 0%——运营误导 P0）。
- PG 不可达 → 显式 file_fallback，禁止静默。
- INVALID run 的 validity/invalid_reason 随列表透出（管理端「环境无效」徽章）。
"""
from __future__ import annotations

import asyncio
import json

from backend.app.api.routes.evaluation import (
    _load_run_rows_from_pg,
    list_eval_runs,
)


def _pg_row(run_id: str, module: str, pass_rate: float, **over) -> dict:
    row = {
        "run_id": run_id,
        "module": module,
        "mode": "offline",
        "status": "completed",
        "validity": "VALID",
        "invalid_reason": "",
        "case_count": 45,
        "pass_count": 42,
        "pass_rate": pass_rate,
        "metrics": {
            module: {
                "total": 45, "passed": 42, "failed": 3, "errors": 0, "skipped": 0,
                "pass_rate": pass_rate,
                "metrics": {"router_hit": 1.0, "safety_pass": 1.0},
            },
        },
        "dataset_version": {"dataset_version": "1.0", "suite": "live"},
        "git_sha": "abc1234",
        "trigger": "manual",
        "triggered_by": "tester",
        "created_at": "2026-10-06T02:22:53",
    }
    row.update(over)
    return row


def test_eval_run_list_uses_pg_summary(monkeypatch):
    """A1 语义：SQL 42/45 → 列表 pass_rate=93.3%（旧实现恒 0% 的回归锁）。"""
    rows = [
        _pg_row("sql-offline-1", "sql", 0.9333),
        _pg_row("rag-golden-1", "rag", 1.0, case_count=99, pass_count=99),
    ]
    monkeypatch.setattr(
        "backend.app.api.routes.evaluation._load_run_rows_from_pg",
        lambda limit: rows,
    )
    summaries = asyncio.run(list_eval_runs(limit=20))
    assert all(s.summary_source == "postgres" for s in summaries)
    sql = next(s for s in summaries if s.run_id == "sql-offline-1")
    assert sql.module == "sql"
    assert abs(sql.pass_rate - 0.9333) < 1e-6
    assert sql.case_count == 45 and sql.pass_count == 42


def test_sql_run_pass_rate_not_zero(monkeypatch):
    """禁绝「SQL 真实通过率显示 0」——即使 metrics JSONB 里 rag 不存在。"""
    rows = [_pg_row("sql-only-1", "sql", 0.9333)]
    monkeypatch.setattr(
        "backend.app.api.routes.evaluation._load_run_rows_from_pg",
        lambda limit: rows,
    )
    summary = asyncio.run(list_eval_runs(limit=10))[0]
    assert summary.pass_rate != 0.0
    assert summary.pass_rate == rows[0]["pass_rate"]


def test_invalid_run_carries_validity_fields(monkeypatch):
    """A3 语义：budget 全挂 run 列表带 validity=INVALID_*，前端显示环境无效。"""
    rows = [_pg_row(
        "budget-dead-1", "sql", 0.0,
        validity="INVALID_BUDGET", invalid_reason="request_budget_exhausted",
        pass_count=0,
    )]
    monkeypatch.setattr(
        "backend.app.api.routes.evaluation._load_run_rows_from_pg",
        lambda limit: rows,
    )
    s = asyncio.run(list_eval_runs(limit=10))[0]
    assert s.validity == "INVALID_BUDGET"
    assert s.invalid_reason == "request_budget_exhausted"


def test_pg_unavailable_falls_back_to_files(monkeypatch, tmp_path):
    """PG 不可达 → 文件降级 + summary_source=file_fallback（不静默）。"""
    monkeypatch.setattr(
        "backend.app.api.routes.evaluation._load_run_rows_from_pg",
        lambda limit: None,
    )
    monkeypatch.setattr("backend.evaluation.storage.DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        "backend.app.api.routes.evaluation.list_runs", lambda limit: ["file-run-1"],
    )

    # 构造一份最小 report 文件
    run_dir = tmp_path / "file-run-1"
    run_dir.mkdir()
    (run_dir / "report.json").write_text(json.dumps({
        "timestamp": "2026-10-06T12:00:00",
        "module": "sql",
        "mode": "live",
        "summaries": [{
            "module": "sql", "total": 45, "passed": 42, "failed": 3,
            "errors": 0, "skipped": 0, "pass_rate": 0.9333, "metrics": {},
        }],
        "results": [
            {"case_id": f"S{i}", "module": "sql", "status": "pass",
             "expected": {}, "actual": {}, "metrics": {}}
            for i in range(42)
        ] + [
            {"case_id": f"F{i}", "module": "sql", "status": "fail",
             "expected": {}, "actual": {}, "metrics": {}}
            for i in range(3)
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "meta.json").write_text("{}", encoding="utf-8")

    summaries = asyncio.run(list_eval_runs(limit=10))
    assert len(summaries) == 1
    s = summaries[0]
    assert s.summary_source == "file_fallback"
    assert s.run_id == "file-run-1"
    assert s.module == "sql"
    assert abs(s.pass_rate - 0.9333) < 1e-6
    assert s.case_count == 45 and s.pass_count == 42


def test_pg_engine_error_returns_none_for_fallback(monkeypatch):
    """台账缺列/连接失败 → 返回 None 交给调用方降级（缺 078 列也走此路）。"""
    class _Boom:
        def __enter__(self):
            raise RuntimeError("column validity does not exist")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.infra.db.engine_for", lambda cfg: type("E", (), {"raw_connection": lambda self: _Boom()})(),
    )
    assert _load_run_rows_from_pg(5) is None
