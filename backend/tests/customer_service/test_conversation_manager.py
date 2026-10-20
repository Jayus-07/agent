"""test_conversation_manager.py — ConversationManager unit tests

Uses mock AsyncSession to test manager logic without a real DB.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.errors import ValidationError
from backend.customer_service.managers.conversation_manager import ConversationManager
from backend.customer_service.state_machine import ConvStatus, HandlingMode


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def manager(mock_session):
    return ConversationManager(mock_session)


class TestCreate:
    async def test_create_with_defaults(self, manager, mock_session):
        # STOP CS-A P0-2：tenant_id 必传（fail-closed 拒绝 default 兜底）
        conv = await manager.create(user_id="user-001", tenant_id="default")
        assert conv.user_id == "user-001"
        assert conv.tenant_id == "default"
        assert conv.conversation_status == "open"
        assert conv.handling_mode == "ai"
        assert conv.channel == "web"
        assert conv.conversation_id is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited()

    async def test_create_with_custom_values(self, manager, mock_session):
        conv = await manager.create(
            user_id="user-001",
            channel="app",
            conversation_id="custom-id",
            handling_mode=HandlingMode.WAITING_HUMAN,
            priority="high",
            tenant_id="tenant-x",
        )
        assert conv.conversation_id == "custom-id"
        assert conv.channel == "app"
        assert conv.handling_mode == "waiting_human"
        assert conv.priority == "high"
        assert conv.tenant_id == "tenant-x"

    async def test_create_without_tenant_fail_closed(self, manager, mock_session):
        """STOP CS-A P0-2：缺 tenant 拒绝创建（不静默落 default）。"""
        from backend.customer_service.errors import ValidationError

        with pytest.raises(ValidationError):
            await manager.create(user_id="user-001")


class TestGet:
    async def test_get_returns_conversation(self, manager, mock_session):
        mock_conv = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conv
        mock_session.execute.return_value = mock_result

        result = await manager.get("conv-001")
        assert result is mock_conv

    async def test_get_returns_none_when_not_found(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await manager.get("nonexistent")
        assert result is None


class TestOwnerScopedHistory:
    async def test_list_by_owner_filters_user_and_tenant(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        await manager.list_by_owner("user-1", "tenant-1", limit=10)

        statement = mock_session.execute.await_args.args[0]
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        assert "user_id = 'user-1'" in sql
        assert "tenant_id = 'tenant-1'" in sql

    async def test_owner_scoped_rename_and_delete(self, manager, mock_session):
        update_result = MagicMock()
        update_result.rowcount = 1
        mock_session.execute.return_value = update_result
        assert await manager.rename_by_owner(
            "conv-1", "user-1", "tenant-1", "标题",
        )
        update_sql = str(
            mock_session.execute.await_args.args[0].compile(
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "user_id = 'user-1'" in update_sql
        assert "tenant_id = 'tenant-1'" in update_sql

        conv = MagicMock()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = conv
        mock_session.execute.return_value = select_result
        assert await manager.delete_by_owner("conv-1", "user-1", "tenant-1")
        mock_session.delete.assert_awaited_once_with(conv)

    async def test_owner_scoped_history_fails_closed_without_tenant(self, manager):
        with pytest.raises(ValidationError, match="tenant_id"):
            await manager.list_by_owner("user-1", "")


class TestTransition:
    async def test_resolve_sets_closed_at(self, manager, mock_session):
        mock_conv = MagicMock()
        mock_conv.conversation_status = "open"
        mock_conv.handling_mode = "ai"
        mock_conv.assigned_agent_id = None
        mock_conv.closed_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conv
        mock_session.execute.return_value = mock_result

        result = await manager.resolve("conv-001")
        assert result.conversation_status == ConvStatus.RESOLVED
        assert mock_conv.closed_at is not None

    async def test_transition_nonexistent_raises(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with pytest.raises(ValidationError, match="not found"):
            await manager.resolve("nonexistent")

    async def test_reopen(self, manager, mock_session):
        mock_conv = MagicMock()
        mock_conv.conversation_status = "resolved"
        mock_conv.handling_mode = "ai"
        mock_conv.assigned_agent_id = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conv
        mock_session.execute.return_value = mock_result

        result = await manager.reopen("conv-001")
        assert result.conversation_status == ConvStatus.OPEN


class TestEscalateToHuman:
    async def test_escalate_sets_agent_and_mode(self, manager, mock_session):
        mock_conv = MagicMock()
        mock_conv.conversation_status = "open"
        mock_conv.handling_mode = "ai"
        mock_conv.assigned_agent_id = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conv
        mock_session.execute.return_value = mock_result

        result = await manager.escalate_to_human("conv-001", "agent-001")
        assert result.handling_mode == HandlingMode.HUMAN
        assert mock_conv.assigned_agent_id == "agent-001"


class TestHandBackToAi:
    async def test_hand_back_clears_agent(self, manager, mock_session):
        mock_conv = MagicMock()
        mock_conv.conversation_status = "open"
        mock_conv.handling_mode = "human"
        mock_conv.assigned_agent_id = "agent-001"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conv
        mock_session.execute.return_value = mock_result

        result = await manager.hand_back_to_ai("conv-001")
        assert result.handling_mode == HandlingMode.AI
        assert mock_conv.assigned_agent_id is None


class TestMetadata:
    async def test_update_summary(self, manager, mock_session):
        mock_exec_result = MagicMock()
        mock_exec_result.rowcount = 1
        mock_session.execute.return_value = mock_exec_result

        ok = await manager.update_summary("conv-001", "New summary")
        assert ok is True

    async def test_set_priority(self, manager, mock_session):
        mock_exec_result = MagicMock()
        mock_exec_result.rowcount = 1
        mock_session.execute.return_value = mock_exec_result

        ok = await manager.set_priority("conv-001", "urgent")
        assert ok is True
