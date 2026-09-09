"""test_confirmation_repo.py — ConfirmationRepository 单元测试 (mock DB)"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.repository.confirmation_repo import (
    ConfirmationRepository,
    _parse_dt,
)


def _make_repo():
    session = AsyncMock()
    repo = ConfirmationRepository(session)
    return repo, session


class TestConfirmationRepositoryLoad:
    @pytest.mark.asyncio
    async def test_load_returns_none_when_empty(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.load("user1", "session1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_returns_row_when_exists(self):
        repo, session = _make_repo()
        row = MagicMock()
        row.confirmation_id = "conf-123"
        row.user_id = "user1"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.load("user1", "session1")
        assert result is row


class TestConfirmationRepositorySave:
    @pytest.mark.asyncio
    async def test_save_creates_row(self):
        repo, session = _make_repo()
        session.add = MagicMock()
        session.flush = AsyncMock()

        pending = {
            "action_id": "act-1",
            "action_type": "refund_request",
            "target_type": "order",
            "target_id": "ORD-1",
            "confirmation_state": "pending",
            "expires_at": "2026-09-05T00:00:00+00:00",
        }
        row = await repo.save("user1", "session1", pending)

        session.add.assert_called_once()
        session.flush.assert_called_once()
        assert row.user_id == "user1"
        assert row.confirmation_id == "act-1"


class TestConfirmationRepositoryUpdateState:
    @pytest.mark.asyncio
    async def test_update_state(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.update_state("conf-1", "confirmed")
        assert ok is True
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_state_not_found(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 0
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.update_state("nonexistent", "confirmed")
        assert ok is False


class TestConfirmationRepositoryClear:
    @pytest.mark.asyncio
    async def test_clear_cancels_pending(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.clear("user1", "session1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_clear_nothing_to_clear(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 0
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.clear("user1", "session1")
        assert ok is False


class TestConfirmationRepositoryHasPending:
    @pytest.mark.asyncio
    async def test_has_pending_true(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar.return_value = True
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.has_pending("user1") is True

    @pytest.mark.asyncio
    async def test_has_pending_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar.return_value = False
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.has_pending("user1") is False


class TestParseDt:
    def test_none_returns_now(self):
        result = _parse_dt(None)
        assert isinstance(result, datetime)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 9, 5, tzinfo=timezone.utc)
        assert _parse_dt(dt) is dt

    def test_iso_string(self):
        result = _parse_dt("2026-09-05T12:00:00+00:00")
        assert result.year == 2026
        assert result.month == 9
