"""SQLAlchemy ORM for memory_records"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Float, Integer, Boolean, DateTime, ForeignKey,
    Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
from pgvector.sqlalchemy import Vector

# 复用 RAG 向量库的统一维度配置（text-embedding-v3 = 1024，env VECTOR_PG_DIM 可覆盖）。
# 历史 bug：此处曾声明 Vector(512)，与 provider 维度不符（台账外修复，2026-09-21）。
from backend.rag.vectorstore.pgvector_store import EMBEDDING_DIM

Base = declarative_base()

class MemoryRecord(Base):
    __tablename__ = "memory_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # scope（048，STOP C）：tenant_id 经 normalize_tenant_id 归一，
    # quarantine=迁移保留哨兵（无身份表映射的 legacy 行，运行时永不产出）
    tenant_id = Column(String(64), nullable=False, default="default")
    user_id = Column(String(64), nullable=False, default="default")
    session_id = Column(String(128), nullable=False, default="")
    memory_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM))
    importance_score = Column(Float, nullable=False, default=0.5)
    confidence_score = Column(Float, nullable=False, default=1.0)
    # provenance（047，STOP B）：写入通道 + 证据消息追溯。
    # origin 由代码层强制赋值（不信任模型输出）；source_message_id 指向
    # role=user 的 chat_messages.id，仅逻辑外键（会话级联删除不得波及长期记忆）
    origin = Column(String(16), nullable=False, default="legacy")
    source_message_id = Column(Integer, nullable=True)
    # 事实版本管理（048，STOP C）：key=属性身份，value=规范化属性值，成对出现；
    # 同 (tenant,user,key) 最多一个 active（partial unique index uq_memory_active_key）
    memory_key = Column(String(128), nullable=True)
    structured_value = Column(String(256), nullable=True)
    # memory 领域隔离与动态画像契约（088）：scope 仅能由服务端策略确定；
    # verification_status=pending 的自动推断不得注入上下文或用户画像。
    scope = Column(String(24), nullable=False, default="user_domain")
    domain = Column(String(32), nullable=False, default="general")
    version = Column(Integer, nullable=False, default=1)
    verification_status = Column(String(24), nullable=False, default="legacy")
    access_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_access_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expire_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memory_records.id"), nullable=True)


class MemoryExtractionJob(Base):
    """持久 L3 提取队列；broker payload 仅传 job UUID，不传对话正文。"""
    __tablename__ = "memory_extraction_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "source_message_id",
            name="uq_memory_extraction_source",
        ),
        Index("idx_memory_extraction_dispatch", "status", "last_enqueued_at"),
        Index("idx_memory_extraction_session", "tenant_id", "user_id", "session_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    source_message_id = Column(Integer, nullable=False)
    assistant_message_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    last_enqueued_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc),
                         onupdate=lambda: datetime.now(timezone.utc))
