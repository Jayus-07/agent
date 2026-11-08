"""PG 派生/镜像 trace 表的时间清理（2026-11-08 安全审计 T7c）。

锁定：trace_summary 与 ai.trace_records 都按 14 天默认留存期批量删除，
语句覆盖两张表，失败只计数不中断。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from backend.observability import trace_retention


class _Result:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int = 0, fail: bool = False,
                 rowcounts: list[int] | None = None):
        self.rowcount = rowcount
        self.rowcounts = list(rowcounts or [])
        self.fail = fail
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt, params):
        if self.fail:
            raise RuntimeError("db down")
        self.statements.append((str(stmt), params))
        return _Result(self.rowcounts.pop(0) if self.rowcounts else self.rowcount)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _patch(monkeypatch, session):
    monkeypatch.setattr("backend.memory.database.AsyncSessionLocal", lambda: session)


def test_purges_both_tables(monkeypatch):
    session = _FakeSession(rowcount=0)
    _patch(monkeypatch, session)

    stats = asyncio.run(
        trace_retention.purge_expired_pg_traces(batch_size=10)
    )

    assert stats == {"summary_purged": 0, "mirror_purged": 0, "errors": 0}
    assert len(session.statements) == 2
    summary_sql, summary_params = session.statements[0]
    mirror_sql, mirror_params = session.statements[1]
    assert "trace_summary" in summary_sql
    assert "created_at <" in summary_sql
    assert isinstance(summary_params["cut"], str)  # localtime 文本
    assert "ai.trace_records" in mirror_sql
    assert isinstance(mirror_params["cut"], datetime)  # timestamptz
    assert session.commits == 2


def test_batches_until_smaller_than_batch(monkeypatch):
    session = _FakeSession(rowcounts=[10, 0, 10, 0])
    _patch(monkeypatch, session)

    asyncio.run(trace_retention.purge_expired_pg_traces(batch_size=10))

    # 每表至少两轮：第一轮满批（10），第二轮 0 收尾
    assert len(session.statements) == 4
    assert session.commits == 4


def test_failure_is_counted_not_raised(monkeypatch):
    session = _FakeSession(fail=True)
    _patch(monkeypatch, session)

    stats = asyncio.run(trace_retention.purge_expired_pg_traces())

    assert stats["errors"] == 2
    assert stats["summary_purged"] == 0
    assert session.rollbacks == 2
