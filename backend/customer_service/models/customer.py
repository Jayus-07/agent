"""CS Customer ORM model

Maps to ``customer_service.customers`` table (new in Alembic 0003).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSCustomer(CSBase):
    __tablename__ = "customers"
    __table_args__ = (
        Index("idx_cs_customer_external", "auth_provider", "auth_external_id", unique=True),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), unique=True, nullable=False)

    display_name = Column(String(128), nullable=False, default="")
    email = Column(String(128), nullable=True)
    phone = Column(String(20), nullable=True)
    avatar_url = Column(Text, nullable=True)

    auth_provider = Column(String(20), nullable=True)
    auth_external_id = Column(String(128), nullable=True)

    custom_fields = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
