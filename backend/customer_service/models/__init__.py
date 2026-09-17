"""customer_service/models — CS ORM models

All models live in the ``customer_service`` PostgreSQL schema.
A dedicated ``CSBase`` keeps them isolated from the memory-layer Base.
"""
from backend.customer_service.models.agent import CSAgent
from backend.customer_service.models.assignment import CSAssignment
from backend.customer_service.models.confirmation import CSConfirmation
from backend.customer_service.models.conversation import CSConversation
from backend.customer_service.models.customer import CSCustomer
from backend.customer_service.models.event import CSEvent
from backend.customer_service.models.handoff import CSHandoff
from backend.customer_service.models.message import CSMessage

__all__ = [
    "CSConversation",
    "CSMessage",
    "CSCustomer",
    "CSAgent",
    "CSAssignment",
    "CSConfirmation",
    "CSEvent",
    "CSHandoff",
]
