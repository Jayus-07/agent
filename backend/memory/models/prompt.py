"""SQLAlchemy ORM for prompt management tables"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Boolean, DateTime, ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(256), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    category = Column(String(64), nullable=False, default="")
    risk_level = Column(String(16), nullable=False, default="low")
    template_engine = Column(String(32), nullable=False, default="str_format")
    variables = Column(JSONB, nullable=False, default=list)
    active_version = Column(Integer, nullable=True)
    is_code_controlled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    versions = relationship(
        "PromptVersion",
        back_populates="prompt",
        cascade="all, delete-orphan",
        order_by="PromptVersion.version.desc()",
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="uq_prompt_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id = Column(
        Integer,
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    template = Column(Text, nullable=False)
    variables = Column(JSONB, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="draft")
    change_note = Column(Text, nullable=False, default="")
    created_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    prompt = relationship("Prompt", back_populates="versions")


class PromptAuditLog(Base):
    __tablename__ = "prompt_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_key = Column(String(128), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    from_version = Column(Integer, nullable=True)
    to_version = Column(Integer, nullable=True)
    actor = Column(String(128), nullable=False, default="system")
    role = Column(String(32), nullable=False, default="")
    detail = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
