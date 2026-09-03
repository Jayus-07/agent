"""test_message_manager.py — MessageManager unit tests

Uses mock AsyncSession to test manager logic without a real DB.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service.managers.message_manager import MessageManager


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def manager(mock_session):
    return MessageManager(mock_session)


class TestCreate:
    async def test_create_user_message(self, manager, mock_session):
        msg = await manager.create(
            conversation_id="conv-001",
            content="Hello",
            sender_type="user",
        )
        assert msg.conversation_id == "conv-001"
        assert msg.content == "Hello"
        assert msg.sender_type == "user"
        assert msg.content_type == "text"
        assert msg.private is False
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited()

    async def test_create_with_metadata(self, manager, mock_session):
        msg = await manager.create(
            conversation_id="conv-001",
            content="Hello",
            metadata={"source": "web"},
            intent_domain="KNOWLEDGE",
            confidence=0.9,
        )
        assert msg.metadata_ == {"source": "web"}
        assert msg.intent_domain == "KNOWLEDGE"
        assert msg.confidence == 0.9


class TestSaveTypedMessages:
    async def test_save_user_message(self, manager, mock_session):
        msg = await manager.save_user_message("conv-001", "Hi")
        assert msg.sender_type == "user"

    async def test_save_assistant_message(self, manager, mock_session):
        msg = await manager.save_assistant_message("conv-001", "Hello!")
        assert msg.sender_type == "assistant"

    async def test_save_system_message_is_private(self, manager, mock_session):
        msg = await manager.save_system_message("conv-001", "Internal note")
        assert msg.sender_type == "system"
        assert msg.private is True

    async def test_save_human_agent_message(self, manager, mock_session):
        msg = await manager.save_human_agent_message(
            "conv-001", "Let me help", sender_id="agent-001",
        )
        assert msg.sender_type == "human_agent"
        assert msg.sender_id == "agent-001"


class TestSaveTurn:
    async def test_save_turn_returns_pair(self, manager, mock_session):
        q, a = await manager.save_turn("conv-001", "Question?", "Answer.")
        assert q.sender_type == "user"
        assert q.content == "Question?"
        assert a.sender_type == "assistant"
        assert a.content == "Answer."


class TestLoadMessages:
    async def test_load_messages(self, manager, mock_session):
        mock_msgs = [MagicMock(), MagicMock()]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_msgs
        mock_session.execute.return_value = mock_result

        msgs = await manager.load_messages("conv-001")
        assert len(msgs) == 2

    async def test_load_with_limit(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        msgs = await manager.load_messages("conv-001", limit=10)
        assert msgs == []


class TestMessageCount:
    async def test_count(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_session.execute.return_value = mock_result

        count = await manager.message_count("conv-001")
        assert count == 5

    async def test_count_zero(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute.return_value = mock_result

        count = await manager.message_count("conv-001")
        assert count == 0


class TestGetLastMessage:
    async def test_get_last(self, manager, mock_session):
        mock_msg = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_msg
        mock_session.execute.return_value = mock_result

        msg = await manager.get_last_message("conv-001")
        assert msg is mock_msg

    async def test_get_last_none(self, manager, mock_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        msg = await manager.get_last_message("conv-001")
        assert msg is None
