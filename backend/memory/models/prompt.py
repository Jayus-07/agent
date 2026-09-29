"""SQLAlchemy ORM for prompt management tables"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
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
        CheckConstraint(
            "status IN ('draft','testing','evaluation','passed','published','archived')",
            name="ck_prompt_version_status",
        ),
        CheckConstraint(
            "change_kind IS NULL OR change_kind IN ('major','minor','patch')",
            name="ck_prompt_version_change_kind",
        ),
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
    # M4（台账 D4）：版本语义（major=行为改变/minor=能力增强/patch=文字修复）。
    # NULL = 存量版本（语义未标注），新版本建议必填（API 层校验）。
    change_kind = Column(String(8), nullable=True)
    change_note = Column(Text, nullable=False, default="")
    created_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    prompt = relationship("Prompt", back_populates="versions")


class PromptAlias(Base):
    """M4（台账 D4）：prompt 命名指针（production/staging）。

    production 与 prompts.active_version 保持同步（切 production = 发布，
    走 publish 语义）；staging 独立指向（预发验收用），不参与运行时读路径
    ——读路径仍走 active_version，staging 消费属 Phase 2 灰度。
    """

    __tablename__ = "prompt_aliases"
    __table_args__ = (
        UniqueConstraint("prompt_id", "alias", name="uq_prompt_alias"),
        CheckConstraint("alias IN ('production','staging')", name="ck_prompt_alias"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_id = Column(
        Integer,
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False)
    updated_by = Column(String(128), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
