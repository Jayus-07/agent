"""test_confirmation_repo.py — ConfirmationRepository 单元测试 (mock DB)"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

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

    @pytest.mark.asyncio
    async def test_load_scopes_pending_row_to_tenant(self):
        repo, session = _make_repo()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        await repo.load("user1", "session1", tenant_id="tenant-1")

        statement = session.execute.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "customer_service.confirmations.tenant_id" in str(compiled)
        assert "tenant-1" in compiled.params.values()


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

    @pytest.mark.asyncio
    async def test_save_persists_proposal_version(self):
        repo, session = _make_repo()
        session.add = MagicMock()
        session.flush = AsyncMock()

        row = await repo.save(
            "user1", "session1", {"action_id": "act-1", "version": 4},
            tenant_id="tenant-1",
        )

        assert row.proposal_version == 4
        assert row.tenant_id == "tenant-1"


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


class TestConfirmationRepositoryVersionedClaim:
    @pytest.mark.asyncio
    async def test_claim_binds_tenant_proposal_version_expiry_and_action_id(self):
        from types import SimpleNamespace

        repo, session = _make_repo()
        conversation_result = MagicMock()
        conversation_result.scalar_one_or_none.return_value = SimpleNamespace(
            user_id="user1", tenant_id="tenant-1", handling_mode="ai",
        )
        handoff_result = MagicMock()
        handoff_result.scalar_one_or_none.return_value = None
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = ["act-1"]
        session.execute = AsyncMock(
            side_effect=[conversation_result, handoff_result, result_mock],
        )
        session.flush = AsyncMock()

        claimed = await repo.claim_pending(
            "user1",
            "session1",
            tenant_id="tenant-1",
            proposal_id="act-1",
            expected_version=4,
            client_action_id="d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
        )

        statement = session.execute.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert claimed == "act-1"
        assert "proposal_version" in str(compiled)
        assert "expires_at" in str(compiled)
        assert "tenant_id" in str(compiled)
        assert "client_action_id" in str(compiled)
        assert "act-1" in compiled.params.values()
        assert 4 in compiled.params.values()
        assert "tenant-1" in compiled.params.values()

    @pytest.mark.asyncio
    async def test_claim_locks_owned_conversation_and_blocks_active_handoff(self):
        from types import SimpleNamespace

        repo, session = _make_repo()
        conversation = SimpleNamespace(
            user_id="user1", tenant_id="tenant-1", handling_mode="ai",
        )
        conversation_result = MagicMock()
        conversation_result.scalar_one_or_none.return_value = conversation
        handoff_result = MagicMock()
        handoff_result.scalar_one_or_none.return_value = None
        claim_result = MagicMock()
        claim_result.scalars.return_value.all.return_value = ["act-1"]
        session.execute = AsyncMock(
            side_effect=[conversation_result, handoff_result, claim_result],
        )
        session.flush = AsyncMock()

        claimed = await repo.claim_pending(
            "user1", "session1", tenant_id="tenant-1",
            proposal_id="act-1", expected_version=2,
        )

        statements = [call.args[0] for call in session.execute.await_args_list]
        conversation_sql = str(statements[0].compile(dialect=postgresql.dialect()))
        handoff_sql = str(statements[1].compile(dialect=postgresql.dialect()))
        assert claimed == "act-1"
        assert "FOR UPDATE" in conversation_sql
        assert "customer_service.conversations" in conversation_sql
        assert "customer_service.handoffs" in handoff_sql

    @pytest.mark.asyncio
    async def test_claim_refuses_when_authoritative_handoff_is_active(self):
        from types import SimpleNamespace

        repo, session = _make_repo()
        conversation_result = MagicMock()
        conversation_result.scalar_one_or_none.return_value = SimpleNamespace(
            user_id="user1", tenant_id="tenant-1", handling_mode="ai",
        )
        handoff_result = MagicMock()
        handoff_result.scalar_one_or_none.return_value = SimpleNamespace(
            handoff_state="agent_offered",
        )
        session.execute = AsyncMock(
            side_effect=[conversation_result, handoff_result],
        )
        session.flush = AsyncMock()

        claimed = await repo.claim_pending(
            "user1", "session1", tenant_id="tenant-1",
            proposal_id="act-1", expected_version=2,
        )

        assert claimed is None
        assert session.execute.await_count == 2


class TestConfirmationRepositoryVersionedCancel:
    @pytest.mark.asyncio
    async def test_cancel_binds_tenant_proposal_version_expiry_and_action_id(self):
        repo, session = _make_repo()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)
        session.flush = AsyncMock()

        cancelled = await repo.cancel_pending(
            "user1", "session1", tenant_id="tenant-1",
            proposal_id="act-1", expected_version=4,
            client_action_id="d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
        )

        statement = session.execute.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert cancelled is True
        assert "proposal_version" in str(compiled)
        assert "expires_at" in str(compiled)
        assert "tenant_id" in str(compiled)
        assert "client_action_id" in str(compiled)
        assert "act-1" in compiled.params.values()
        assert 4 in compiled.params.values()
        assert "tenant-1" in compiled.params.values()
        assert "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11" in compiled.params.values()


class TestConfirmationRepositoryProposalVersion:
    @pytest.mark.asyncio
    async def test_proposal_update_is_compare_and_swap_on_pending_version(self):
        repo, session = _make_repo()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)
        session.flush = AsyncMock()

        await repo.update_proposal(
            "act-1", {"action_id": "act-1", "target_id": "order-new"},
            proposal_version=5,
        )

        statement = session.execute.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "proposal_version" in str(compiled)
        assert "state" in str(compiled)
        assert 4 in compiled.params.values()
        assert "pending" in compiled.params.values()


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
