"""test_models.py — CS ORM model unit tests

Tests model construction, defaults, and properties without DB.
"""
import pytest
from datetime import datetime, timezone

from backend.customer_service.models.conversation import CSConversation, CSBase
from backend.customer_service.models.message import CSMessage
from backend.customer_service.models.customer import CSCustomer
from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment


class TestCSConversationModel:
    def test_table_name(self):
        assert CSConversation.__tablename__ == "conversations"

    def test_schema(self):
        assert CSConversation.__table_args__["schema"] == "customer_service"

    def test_defaults(self):
        conv = CSConversation(
            conversation_id="test-001",
            user_id="user-001",
        )
        assert conv.conversation_status == "open"
        assert conv.handling_mode == "ai"
        assert conv.channel == "web"
        assert conv.priority == "medium"
        assert conv.ai_enabled is True

    def test_is_open_property(self):
        conv = CSConversation(
            conversation_id="test-001",
            user_id="user-001",
            conversation_status="open",
        )
        assert conv.is_open is True
        assert conv.is_resolved is False

    def test_is_resolved_property(self):
        conv = CSConversation(
            conversation_id="test-001",
            user_id="user-001",
            conversation_status="resolved",
        )
        assert conv.is_resolved is True
        assert conv.is_open is False

    def test_is_ai_handling_property(self):
        conv = CSConversation(
            conversation_id="test-001",
            user_id="user-001",
            handling_mode="ai",
        )
        assert conv.is_ai_handling is True


class TestCSMessageModel:
    def test_table_name(self):
        assert CSMessage.__tablename__ == "messages"

    def test_schema(self):
        assert CSMessage.__table_args__["schema"] == "customer_service"

    def test_defaults(self):
        msg = CSMessage(
            message_id="msg-001",
            conversation_id="conv-001",
            content="Hello",
        )
        assert msg.sender_type == "user"
        assert msg.content_type == "text"
        assert msg.private is False


class TestCSCustomerModel:
    def test_table_name(self):
        assert CSCustomer.__tablename__ == "customers"

    def test_schema(self):
        assert CSCustomer.__table_args__["schema"] == "customer_service"

    def test_defaults(self):
        customer = CSCustomer(
            customer_id="cust-001",
            display_name="Test User",
        )
        assert customer.display_name == "Test User"
        assert customer.email is None


class TestCSAgentModel:
    def test_table_name(self):
        assert CSAgent.__tablename__ == "cs_agents"

    def test_schema(self):
        assert CSAgent.__table_args__["schema"] == "customer_service"

    def test_defaults(self):
        agent = CSAgent(
            agent_id="agent-001",
            display_name="Agent Smith",
        )
        assert agent.role == "agent"
        assert agent.available is True
        assert agent.max_conversations == 10


class TestCSAssignmentModel:
    def test_table_name(self):
        assert CSAssignment.__tablename__ == "assignments"

    def test_schema(self):
        assert CSAssignment.__table_args__["schema"] == "customer_service"


class TestCSBaseIsolation:
    def test_cs_base_is_separate_from_memory_base(self):
        from backend.memory.models.session import Base as MemoryBase
        assert CSBase is not MemoryBase
