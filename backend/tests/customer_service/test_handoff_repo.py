"""test_handoff_repo.py — HandoffRepository 单元测试 (mock DB)"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.repository.handoff_repo import HandoffRepository


def _make_repo():
    session = AsyncMock()
    repo = HandoffRepository(session)
    return repo, session


class TestHandoffRepositoryLoad:
    @pytest.mark.asyncio
    async def test_load_returns_none_when_empty(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.load("user1", "session1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_returns_row(self):
        repo, session = _make_repo()
        row = MagicMock()
        row.handoff_id = "ho-1"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.load("user1", "session1")
        assert result is row


class TestHandoffRepositorySave:
    @pytest.mark.asyncio
    async def test_save_creates_row(self):
        repo, session = _make_repo()
        session.add = MagicMock()
        session.flush = AsyncMock()

        data = {
            "handoff_state": "handoff_requested",
            "trigger_type": "explicit",
            "trigger_reason": "user request",
            "ticket_id": "TKT-1",
        }
        row = await repo.save("user1", "session1", data)

        session.add.assert_called_once()
        session.flush.assert_called_once()
        assert row.user_id == "user1"
        assert row.handoff_state == "handoff_requested"


class TestHandoffRepositoryUpdateState:
    @pytest.mark.asyncio
    async def test_update_state(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.update_state("ho-1", "closed")
        assert ok is True

    @pytest.mark.asyncio
    async def test_update_state_not_found(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 0
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.update_state("nonexistent", "closed")
        assert ok is False


class TestHandoffRepositoryClear:
    @pytest.mark.asyncio
    async def test_clear_closes_handoff(self):
        repo, session = _make_repo()
        update_result = MagicMock()
        update_result.rowcount = 1
        session.execute = AsyncMock(return_value=update_result)
        session.flush = AsyncMock()

        ok = await repo.clear("user1", "session1")
        assert ok is True


class TestHandoffRepositoryHasActive:
    @pytest.mark.asyncio
    async def test_has_active_true(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar.return_value = True
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.has_active("user1") is True

    @pytest.mark.asyncio
    async def test_has_active_false(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar.return_value = False
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.has_active("user1") is False


class TestHandoffRepositoryGetActive:
    @pytest.mark.asyncio
    async def test_get_active_returns_row(self):
        repo, session = _make_repo()
        row = MagicMock()
        row.handoff_state = "handoff_requested"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_active("user1")
        assert result is row

    @pytest.mark.asyncio
    async def test_get_active_returns_none(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_active("user1")
        assert result is None
