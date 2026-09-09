"""test_models.py — CS ORM model unit tests

Tests model construction, defaults, and properties without DB.
"""

from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.conversation import CSBase, CSConversation
from backend.customer_service.models.customer import CSCustomer
from backend.customer_service.models.message import CSMessage


def _get_schema(model_cls) -> str:
    """Extract schema from __table_args__ tuple (contains Index objects + dict)."""
    for item in model_cls.__table_args__:
        if isinstance(item, dict) and "schema" in item:
            return item["schema"]
    return ""


class TestCSConversationModel:
    def test_table_name(self):
        assert CSConversation.__tablename__ == "conversations"

    def test_schema(self):
        assert _get_schema(CSConversation) == "customer_service"

    def test_defaults(self):
        assert CSConversation.__table__.c.conversation_status.default.arg == "open"
        assert CSConversation.__table__.c.handling_mode.default.arg == "ai"
        assert CSConversation.__table__.c.channel.default.arg == "web"
        assert CSConversation.__table__.c.priority.default.arg == "medium"
        assert CSConversation.__table__.c.ai_enabled.default.arg is True

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
        assert _get_schema(CSMessage) == "customer_service"

    def test_defaults(self):
        assert CSMessage.__table__.c.sender_type.default.arg == "user"
        assert CSMessage.__table__.c.content_type.default.arg == "text"
        assert CSMessage.__table__.c.private.default.arg is False


class TestCSCustomerModel:
    def test_table_name(self):
        assert CSCustomer.__tablename__ == "customers"

    def test_schema(self):
        assert _get_schema(CSCustomer) == "customer_service"

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
        assert _get_schema(CSAgent) == "customer_service"

    def test_defaults(self):
        assert CSAgent.__table__.c.role.default.arg == "agent"
        assert CSAgent.__table__.c.available.default.arg is True
        assert CSAgent.__table__.c.max_conversations.default.arg == 10


class TestCSAssignmentModel:
    def test_table_name(self):
        assert CSAssignment.__tablename__ == "assignments"

    def test_schema(self):
        assert _get_schema(CSAssignment) == "customer_service"


class TestCSBaseIsolation:
    def test_cs_base_is_separate_from_memory_base(self):
        from backend.memory.models.session import Base as MemoryBase
        assert CSBase is not MemoryBase
