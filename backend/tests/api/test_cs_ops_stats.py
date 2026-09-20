"""P8 运营统计端点测试（/cs/ops/dispatch/stats）。

直接调用路由函数：repository 计数与 AsyncSessionLocal 都被脚本化，
``require_admin_user`` 门禁由统一依赖保证（P2 治理已有端到端覆盖）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.app.api.routes import cs_ops
from backend.customer_service.dispatch import repository

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class _FakeSession:
    pass


class _FakeSessionLocal:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, *_a):
        return False


def _install(monkeypatch: pytest.MonkeyPatch, *, oldest=None) -> None:
    async def count_queue(*_a, **_k):
        return {"tenant-a": 3, "tenant-b": 1}

    async def count_offering(*_a, **_k):
        return {"tenant-a": 2}

    async def count_enabled(*_a, **_k):
        return {"tenant-a": 4}

    async def count_pending(*_a, **_k):
        return 7

    async def oldest_pending(*_a, **_k):
        return oldest

    monkeypatch.setattr(repository, "count_queue_by_tenant", count_queue)
    monkeypatch.setattr(repository, "count_offering_by_tenant", count_offering)
    monkeypatch.setattr(repository, "count_enabled_agents_by_tenant", count_enabled)
    monkeypatch.setattr(repository, "count_pending_outbox", count_pending)
    monkeypatch.setattr(repository, "oldest_pending_outbox_created_at", oldest_pending)
    monkeypatch.setattr(cs_ops, "AsyncSessionLocal", _FakeSessionLocal())


async def test_dispatch_stats_returns_snapshot(monkeypatch) -> None:
    _install(monkeypatch, oldest=NOW - timedelta(seconds=2))

    data = await cs_ops.dispatch_stats(_operator())

    assert data["queue"]["waiting_total"] == 4
    assert data["queue"]["offered_total"] == 2
    assert data["queue"]["waiting_by_tenant"] == {"tenant-a": 3, "tenant-b": 1}
    assert data["agents"]["enabled_total"] == 4
    assert data["outbox"]["pending"] == 7
    assert data["outbox"]["oldest_pending_lag_seconds"] >= 2.0
    assert "generated_at" in data


async def test_dispatch_stats_without_pending_events_has_zero_lag(
    monkeypatch,
) -> None:
    _install(monkeypatch, oldest=None)

    data = await cs_ops.dispatch_stats(_operator())

    assert data["outbox"]["pending"] == 7  # pending 计数与 oldest 解耦
    assert data["outbox"]["oldest_pending_lag_seconds"] == 0.0


class _Operator:
    role = "admin"
    actor = "user:admin-1"
    kind = "user"


def _operator():
    return _Operator()


async def test_dispatch_stats_maps_db_unavailable(monkeypatch) -> None:
    from backend.memory.database import MemoryDatabaseUnavailable

    class _BrokenLocal:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise MemoryDatabaseUnavailable("db down")

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(cs_ops, "AsyncSessionLocal", _BrokenLocal())

    with pytest.raises(HTTPException) as exc_info:
        await cs_ops.dispatch_stats(_operator())

    assert exc_info.value.status_code == 503
